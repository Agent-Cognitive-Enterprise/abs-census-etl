"""Offline positive and negative replay controls using realistic synthetic ZIPs."""
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from zipfile import ZIP_DEFLATED, ZipFile

import evidence


def make_evidence(root):
    root.mkdir()
    requests = []

    def archive_bytes(members):
        output = io.BytesIO()
        with ZipFile(output, 'w', compression=ZIP_DEFLATED) as archive:
            for name, body in members.items():
                archive.writestr(name, body)
        return output.getvalue()

    def saved_range(geo, state, codes):
        column = 'SA2_MAINCODE_2016' if geo == 'SA2' else f'{geo}_CODE_2016'
        member = f'2016 Census GCP data/2016Census_G01_{state}_{geo}.csv'
        rows = column + ',Tot_P_P\n' + ''.join(f'{code},{people}\n' for code, people in codes)
        body = archive_bytes({member: rows})
        path = root / f'{geo}-{state}.bin'
        path.write_bytes(body)
        requests.append({'url': evidence.BASE + f'2016_GCP_{geo}_for_{state}_short-header.zip',
                         'status': 206, 'file': path.name, 'bytes_received': len(body),
                         'sha256': hashlib.sha256(body).hexdigest(),
                         'headers': {'Content-Range': f'bytes 0-{len(body)-1}/{len(body)}'}})

    for geo, state, codes in [
        ('POA', 'NT', [('POA0800', 6464), ('POA0810', 33302)]),
        ('SA2', 'NSW', [('101021007', 3872)]),
        ('LGA', 'NSW', [('LGA17200', 208374)]),
        ('POA', 'AUS', [('POA2000', 27411)]),
    ]:
        saved_range(geo, state, codes)
    local_members = {
        '2016 Census GCP Postal Areas for NSW/2016Census_G01_NSW_POA.csv':
            'POA_CODE_2016,Tot_P_P\nPOA2000,27411\nPOA2010,27734\n',
        'Metadata/Metadata_2016_GCP_DataPack.xlsx': b'not parsed by population replay',
        'Readme/2016POA_readme.txt': b'\x93Postal areas\x94',
        'Readme/esri_arcmap_readme.txt': b'\xef\xbb\xbfUTF-8 with mark',
        'Readme/Formats_readme.txt': b'Plain ASCII',
        'Readme/Unicode_example.txt': 'UTF-8 café',
    }
    (root / '2016_GCP_POA_for_NSW_short-header.zip').write_bytes(archive_bytes(local_members))
    other = archive_bytes({'2021 Census IP Local Government Areas for NSW/'
                           '2021Census_I01A_NSW_LGA.csv': 'LGA_CODE_2021,example\n'})
    (root / 'IP.bin').write_bytes(other)
    requests.append({'url': evidence.BASE + '2021_IP_LGA_for_NSW_short-header.zip',
                     'status': 206, 'file': 'IP.bin', 'bytes_received': len(other),
                     'headers': {'Content-Range': f'bytes 0-{len(other)-1}/{len(other)}'}})
    (root / '404.bin').write_bytes(b'not found')
    requests.append({'url': 'https://example.invalid/missing', 'status': 404,
                     'file': '404.bin', 'bytes_received': 9,
                     'error': 'Server ignored bounded range'})
    total = sum(item['bytes_received'] for item in requests)
    (root / 'ledger.json').write_text(json.dumps({'requests': requests,
                                                 'bytes_received': total, 'limit_bytes': total}))
    for code, people in [('POA0800', 6464), ('101021007', 3872),
                         ('LGA17200', 208374), ('POA2000', 27411)]:
        (root / f'quickstats-{code}.html').write_text(
            f'<p>Area code {code} People {people:,} Male 50% Median age 32</p>')
    evidence.create_manifest(root, root / 'manifest.json')


class EvidenceTests(unittest.TestCase):
    def test_manifest_rejects_changed_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source'
            source.write_bytes(b'original')
            manifest = root / 'manifest.json'
            evidence.create_manifest(root, manifest)
            self.assertEqual(evidence.verify_manifest(root, manifest), 1)
            source.write_bytes(b'tampered')
            with self.assertRaisesRegex(ValueError, 'Manifest mismatch'):
                evidence.verify_manifest(root, manifest)

    def test_manifest_refuses_replacement(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'source').write_bytes(b'source')
            manifest = root / 'manifest.json'
            evidence.create_manifest(root, manifest)
            before = manifest.read_bytes()
            with self.assertRaisesRegex(ValueError, 'Refusing to overwrite'):
                evidence.create_manifest(root, manifest)
            self.assertEqual(before, manifest.read_bytes())

    def test_replay_refuses_existing_output_before_reading_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sentinel = root / 'sentinel'
            sentinel.write_bytes(b'preserve')
            with self.assertRaisesRegex(ValueError, 'Refusing to overwrite'):
                evidence.main(['replay', '--evidence', str(root / 'missing'),
                               '--output', str(root)])
            self.assertEqual(sentinel.read_bytes(), b'preserve')

    def test_saved_ranges_refuse_gaps_and_never_fill_with_zeros(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'body').write_bytes(b'ABC')
            entries = [{'url': 'saved', 'status': 206, 'file': 'body',
                        'headers': {'Content-Range': 'bytes 0-2/6'}}]
            stream = evidence.SavedRanges(root, entries, 'saved')
            self.assertEqual(stream.read(3), b'ABC')
            with self.assertRaisesRegex(ValueError, 'Unrecorded archive bytes'):
                stream.read(1)

    def test_manifest_paths_cannot_escape_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / 'manifest.json'
            manifest.write_text(json.dumps({'files': {'../outside': {'bytes': 1}}}))
            with self.assertRaisesRegex(ValueError, 'Unsafe evidence path'):
                evidence.verify_manifest(root, manifest)


    def test_real_replay_compares_populations_corrects_404_and_decodes_readmes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'input'
            make_evidence(root)
            expected = evidence.digest(root / 'manifest.json')
            reports = []
            for name in ['one', 'two']:
                output = Path(directory) / name
                self.assertEqual(evidence.main(['replay', '--evidence', str(root),
                    '--expect-sha256', expected, '--output', str(output)]), 0)
                reports.append((output / 'report.json').read_bytes())
                self.assertEqual(evidence.verify_manifest(output, output / 'manifest.json'), 1)
            self.assertEqual(reports[0], reports[1])
            report = json.loads(reports[0])
            self.assertEqual(len(report['population_comparisons']), 5)
            self.assertEqual(len(report['population_observations']), 2)
            failed = report['corrected_ledger']['requests'][-1]
            self.assertIn('HTTP 404', failed['error'])
            self.assertEqual(failed['original_error'], 'Server ignored bounded range')
            readmes = {Path(item['member']).name: item for item in report['readmes']}
            expected_encodings = {'2016POA_readme.txt': 'cp1252',
                                  'esri_arcmap_readme.txt': 'utf-8-sig',
                                  'Formats_readme.txt': 'ascii',
                                  'Unicode_example.txt': 'utf-8'}
            self.assertEqual({name: item['encoding'] for name, item in readmes.items()},
                             expected_encodings)
            self.assertEqual(readmes['esri_arcmap_readme.txt']['text'], 'UTF-8 with mark')
            self.assertEqual(readmes['2016POA_readme.txt']['text'], '“Postal areas”')

    def test_population_mismatch_stops_replay_before_creating_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'input'
            make_evidence(root)
            page = root / 'quickstats-POA0800.html'
            page.write_text(page.read_text().replace('6,464', '6,465'))
            (root / 'manifest.json').unlink()
            evidence.create_manifest(root, root / 'manifest.json')
            output = Path(directory) / 'output'
            with self.assertRaisesRegex(ValueError, 'Population mismatch: POA0800'):
                evidence.main(['replay', '--evidence', str(root), '--output', str(output)])
            self.assertFalse(output.exists())

    def test_manifest_refuses_unlisted_response(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'source').write_bytes(b'source')
            manifest = root / 'manifest.json'
            evidence.create_manifest(root, manifest)
            (root / 'responses').mkdir()
            (root / 'responses/9999.bin').write_bytes(b'planted')
            with self.assertRaisesRegex(ValueError, 'inventory differs.*9999.bin'):
                evidence.verify_manifest(root, manifest)

    def test_manifest_anchor_detects_replaced_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'source').write_bytes(b'original')
            manifest = root / 'manifest.json'
            evidence.create_manifest(root, manifest)
            expected = evidence.digest(manifest)
            (root / 'source').write_bytes(b'tampered')
            manifest.unlink()
            evidence.create_manifest(root, manifest)
            with self.assertRaisesRegex(ValueError, 'Manifest SHA-256 mismatch'):
                evidence.main(['verify', '--evidence', str(root), '--expect-sha256', expected])

    def test_replay_refuses_nested_output_before_modifying_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / 'new' / 'report'
            with self.assertRaisesRegex(ValueError, 'outside the input evidence'):
                evidence.main(['replay', '--evidence', str(root), '--output', str(output)])
            self.assertEqual(list(root.iterdir()), [])


    def test_cache_exemption_is_only_direct_top_level_pyc(self):
        for extra in ['responses/__pycache__/0093.bin', '__pycache__/notes.txt',
                      'responses/__pycache__/extra.pyc', '__pycache__/nested/extra.pyc']:
            with self.subTest(extra=extra), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / 'source').write_bytes(b'original')
                manifest = root / 'manifest.json'
                evidence.create_manifest(root, manifest)
                allowed = root / '__pycache__/audit.cpython-314.pyc'
                allowed.parent.mkdir()
                allowed.write_bytes(b'interpreter cache')
                self.assertEqual(evidence.verify_manifest(root, manifest), 1)
                planted = root / extra
                planted.parent.mkdir(parents=True, exist_ok=True)
                planted.write_bytes(b'unlisted evidence')
                with self.assertRaisesRegex(ValueError, 'inventory differs'):
                    evidence.verify_manifest(root, manifest)


if __name__ == '__main__':
    unittest.main()
