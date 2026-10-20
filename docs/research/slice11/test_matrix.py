"""Offline acquisition controls; never contact ABS or any dependency index."""
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from zipfile import ZIP_DEFLATED, ZipFile

import matrix


class Raw(io.BytesIO):
    def read(self, size=-1, *, decode_content=False):
        return super().read(size)


class Response:
    def __init__(self, status, body, **headers):
        self.status_code = status
        self.raw = Raw(body)
        self.headers = {'Content-Length': str(len(body)), **headers}

    def __enter__(self): return self
    def __exit__(self, *args): return False


class Session:
    def __init__(self, *responses):
        self.responses = iter(responses)
        self.headers = {}
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return next(self.responses)


def pack(*, rows='POA0800,6464\n', extra=None):
    content = io.BytesIO()
    with ZipFile(content, 'w', compression=ZIP_DEFLATED) as archive:
        archive.writestr('2016 Census GCP Postal Areas for NT/2016Census_G01_NT_POA.csv',
                         'POA_CODE_2016,Tot_P_P\n' + rows)
        archive.writestr('Metadata/Metadata_2016_GCP_DataPack.xlsx', b'workbook-not-read')
        archive.writestr('Readme/AboutDatpacks_readme.txt', b'Availability \x96 GCP')
        if extra:
            archive.writestr(extra, b'conflicting data')
    content.seek(0)
    return ZipFile(content)


class MatrixTests(unittest.TestCase):
    def test_structural_check_preserves_string_code_and_inferred_readme_encoding(self):
        with pack() as archive:
            result = matrix.inspect(archive, 'POA', 'NT')
        self.assertEqual(result['sample_codes'], ['POA0800'])
        self.assertFalse(result['g01_empty'])
        self.assertIsNone(result['full_zip_sha256'])
        self.assertEqual(result['readmes'][0]['decoding'], 'cp1252')
        self.assertTrue(result['readmes'][0]['cp1252_is_inferred'])

    def test_empty_g01_is_a_valid_empty_pack_not_an_absent_endpoint(self):
        with pack(rows='') as archive:
            result = matrix.inspect(archive, 'POA', 'NT')
        self.assertEqual(result['status'], 'verified-present')
        self.assertTrue(result['g01_empty'])
        self.assertEqual(result['sample_codes'], [])

    def test_conflicting_year_profile_state_or_geography_is_refused(self):
        for member in ['2021Census_G01_NT_POA.csv', '2016Census_T01_NT_POA.csv',
                       '2016Census_G01_NSW_POA.csv', '2016Census_G01_NT_SA1.csv']:
            with self.subTest(member=member), pack(extra=member) as archive:
                with self.assertRaisesRegex(ValueError, 'Conflicting data member'):
                    matrix.inspect(archive, 'POA', 'NT')

    def test_range_response_validates_offsets_and_counts_exact_body(self):
        with tempfile.TemporaryDirectory() as directory:
            session = Session(Response(206, b'ABC', **{'Content-Range': 'bytes 4-6/10'}))
            http = matrix.HTTP(Path(directory), limit=3, session=session)
            _, body = http.get('https://example.invalid/pack', start=4, end=6)
            self.assertEqual(body, b'ABC')
            self.assertEqual(http.ledger['bytes_received'], 3)
            with self.assertRaises(matrix.BudgetError):
                http.get('https://example.invalid/pack', start=7, end=7)
            self.assertEqual(len(session.calls), 1)
            self.assertFalse(session.calls[0][1]['allow_redirects'])

    def test_cap_cuts_a_range_without_reading_or_accounting_beyond_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            response = Response(206, b'ABCDEFGHI', **{'Content-Range': 'bytes 0-8/9'})
            http = matrix.HTTP(Path(directory), limit=5, session=Session(response))
            with self.assertRaises(matrix.BudgetError):
                http.get('https://example.invalid/pack', start=0, end=8)
            self.assertEqual(response.raw.tell(), 5)
            self.assertEqual(http.ledger['bytes_received'], 5)
            saved = json.loads((Path(directory) / 'ledger.json').read_text())
            self.assertEqual(saved['requests'][0]['bytes_received'], 5)

    def test_404_is_counted_and_diagnosed_as_http_error(self):
        with tempfile.TemporaryDirectory() as directory:
            http = matrix.HTTP(Path(directory), session=Session(Response(404, b'not found')))
            with self.assertRaisesRegex(matrix.TransferError, 'HTTP 404'):
                http.get('https://example.invalid/missing', start=0, end=0)
            self.assertEqual(http.ledger['bytes_received'], 9)
            self.assertEqual(http.ledger['requests'][0]['status'], 404)

    def test_ignored_range_is_bounded_and_never_becomes_full_download(self):
        with tempfile.TemporaryDirectory() as directory:
            response = Response(200, b'X' * 100000)
            http = matrix.HTTP(Path(directory), session=Session(response))
            with self.assertRaisesRegex(ValueError, 'refused bounded range'):
                http.get('https://example.invalid/pack', start=0, end=0, max_body=16)
            self.assertEqual(response.raw.tell(), 16)
            self.assertEqual(http.ledger['bytes_received'], 16)
            self.assertFalse(http.ledger['requests'][0]['body_complete'])

    def test_redirect_bodies_are_also_saved_and_counted(self):
        with tempfile.TemporaryDirectory() as directory:
            session = Session(Response(302, b'moved', Location='/actual'),
                              Response(206, b'A', **{'Content-Range': 'bytes 0-0/1'}))
            http = matrix.HTTP(Path(directory), session=session)
            _, body = http.get('https://example.invalid/old', start=0, end=0)
            self.assertEqual(body, b'A')
            self.assertEqual(http.ledger['bytes_received'], 6)
            self.assertEqual(len(http.ledger['requests']), 2)
            self.assertEqual(session.calls[1][0], 'https://example.invalid/actual')


def cell(geo, state, parts):
    folder = f'2016 Census GCP {geo} for {state}'
    return {'geography': geo, 'state': state,
            'members': [f'{folder}/2016Census_{part}_{state}_{geo}.csv' for part in parts]
            + ['Metadata/Metadata_2016_GCP_DataPack.xlsx']}


class VerificationTests(unittest.TestCase):
    def test_complete_g01_reads_every_row_and_refuses_duplicate_codes(self):
        with pack(rows='POA0800,6464\nPOA0810,33302\nPOA0812,10\n') as archive:
            self.assertEqual(matrix.complete_g01(archive, 'POA', 'NT'),
                             {'POA0800': 6464, 'POA0810': 33302, 'POA0812': 10})
        with pack(rows='POA0800,6464\nPOA0800,1\n') as archive:
            with self.assertRaisesRegex(ValueError, 'Duplicate G01 codes'):
                matrix.complete_g01(archive, 'POA', 'NT')

    def test_uniform_inventory_refuses_a_cell_with_different_parts(self):
        cells = [cell('POA', 'NSW', ['G01', 'G02A', 'G02B']),
                 cell('CED', 'OT', ['G01', 'G02A', 'G02B'])]
        self.assertEqual(matrix.uniform_inventory(cells), ['G01', 'G02A', 'G02B'])
        cells.append(cell('SA1', 'ACT', ['G01', 'G02A']))
        with self.assertRaisesRegex(ValueError, 'inventory differs: SA1/ACT'):
            matrix.uniform_inventory(cells)

    def test_ot_rows_must_match_the_complete_saved_g01(self):
        content = io.BytesIO()
        with ZipFile(content, 'w', compression=ZIP_DEFLATED) as archive:
            archive.writestr('2016 Census GCP Commonwealth Electoral Divisions for OT/'
                             '2016Census_G01_OT_CED.csv',
                             'CED_CODE_2016,Tot_P_P\nCED994,55\nCED997,6\n')
        body = content.getvalue()
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / 'ced.bin').write_bytes(body)
            entries = [{'url': matrix.BASE + '2016_GCP_CED_for_OT_short-header.zip',
                        'status': 206, 'file': 'ced.bin',
                        'headers': {'Content-Range': f'bytes 0-{len(body) - 1}/{len(body)}'}}]
            expected = {'CED': {'CED994': 55, 'CED997': 6}}
            with mock.patch.dict(matrix.OT_G01_ROWS, expected, clear=True):
                self.assertEqual(matrix.verify_ot_rows(Path(directory), entries), expected)
            changed = {'CED': {'CED994': 55, 'CED997': 7}}
            with mock.patch.dict(matrix.OT_G01_ROWS, changed, clear=True):
                with self.assertRaisesRegex(ValueError, 'OT G01 rows differ: CED'):
                    matrix.verify_ot_rows(Path(directory), entries)


class SupportingDocumentTests(unittest.TestCase):
    def test_primary_publication_uses_its_declared_http_charset(self):
        from supporting import decode_document
        encoding, text = decode_document(b'July 2016 \xa9 ABS',
                                         'text/html; charset=ISO-8859-1')
        self.assertEqual(encoding, 'iso-8859-1')
        self.assertEqual(text, 'July 2016 © ABS')

    def test_declared_utf8_does_not_silently_replace_invalid_bytes(self):
        from supporting import decode_document
        with self.assertRaises(UnicodeDecodeError):
            decode_document(b'bad UTF-8 \xa9', 'text/html; charset=UTF-8')


if __name__ == '__main__':
    unittest.main()
