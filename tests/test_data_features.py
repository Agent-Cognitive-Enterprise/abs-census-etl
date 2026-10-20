"""Offline integrity, discovery, geography and process-lock regressions."""
from __future__ import annotations

import errno
import hashlib
import io
import json
import os
import runpy
import struct
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from xml.etree import ElementTree as E
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import abs_census_etl as etl

NS = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
REL = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
PKG = 'http://schemas.openxmlformats.org/package/2006/relationships'
META = 'Metadata/Metadata_2021_GCP_DataPack_R1_R2.xlsx'
TABLE_ROWS = [[], ['2021 Census of Population and Housing'],
              ['Table Number', 'Table Name', 'Table population '],
              ['G01', 'Selected Person Characteristics by Sex', 'Persons'],
              ['G02', 'Selected Medians and Averages', ''],
              ['G04', 'Age by Sex', 'Persons']]
VARIABLE_ROWS = [['2021 Census of Population and Housing'],
                 ['Sequential', 'Short', 'Long', 'DataPack\nfile', 'Profile\ntable',
                  'Column heading description in profile'],
                 ['G3', 'Tot_P_P', 'Total_Persons_Persons', 'G01', 'G01', 'Persons'],
                 ['G109', 'Median_age_persons', 'Median_age_of_persons', 'G02', 'G02', 'Median age of persons'],
                 ['G257', 'Age_yr_0_M', 'Age_years_0_Males', 'G04A', 'G04', 'Males'],
                 ['G460', 'Age_yr_56_P', 'Age_years_56_Persons', 'G04B', 'G04', 'Persons']]


def workbook(*, shared=False, tables=None, variables=None):
    """Small real-schema workbook: sparse rows, inline or shared/rich text cells."""
    sheets = [('Table Number, Name, Population', TABLE_ROWS if tables is None else tables),
              ('Cell Descriptors Information', VARIABLE_ROWS if variables is None else variables)]
    output = io.BytesIO()
    strings = []
    with ZipFile(output, 'w') as z:
        book = E.Element(f'{{{NS}}}workbook')
        bs = E.SubElement(book, f'{{{NS}}}sheets')
        rels = E.Element(f'{{{PKG}}}Relationships')
        for number, (name, rows) in enumerate(sheets, 1):
            E.SubElement(bs, f'{{{NS}}}sheet', name=name, sheetId=str(number),
                         attrib={f'{{{REL}}}id': f'rId{number}'})
            E.SubElement(rels, f'{{{PKG}}}Relationship', Id=f'rId{number}',
                         Type=REL+'/worksheet', Target=f'worksheets/sheet{number}.xml')
            sheet = E.Element(f'{{{NS}}}worksheet')
            data = E.SubElement(sheet, f'{{{NS}}}sheetData')
            for rownum, values in enumerate(rows, 7):
                row = E.SubElement(data, f'{{{NS}}}row', r=str(rownum))
                for col, value in enumerate(values):
                    if not value:
                        continue
                    cell = E.SubElement(row, f'{{{NS}}}c', r=f'{chr(65+col)}{rownum}',
                                        t='s' if shared else 'inlineStr')
                    if shared:
                        E.SubElement(cell, f'{{{NS}}}v').text = str(len(strings))
                        strings.append(value)
                    else:
                        E.SubElement(E.SubElement(cell, f'{{{NS}}}is'), f'{{{NS}}}t').text = value
            z.writestr(f'xl/worksheets/sheet{number}.xml', E.tostring(sheet))
        z.writestr('xl/workbook.xml', E.tostring(book))
        z.writestr('xl/_rels/workbook.xml.rels', E.tostring(rels))
        if shared:
            ss = E.Element(f'{{{NS}}}sst')
            for text in strings:
                si = E.SubElement(ss, f'{{{NS}}}si')
                # Two rich-text runs must concatenate without invented spaces.
                for half in [text[:3], text[3:]]:
                    E.SubElement(E.SubElement(si, f'{{{NS}}}r'), f'{{{NS}}}t').text = half
            z.writestr('xl/sharedStrings.xml', E.tostring(ss))
    return output.getvalue()


def make_pack(path, *, geo='POA', state='NSW', code='POA2000', token='27936', metadata=None):
    names = dict(etl.GEOGRAPHIES, SA1='Statistical Area 1', SA2='Statistical Area 2',
                 SA3='Statistical Area 3', SA4='Statistical Area 4',
                 SED='State Electroral Division', CED='Commonwealth Electroral Division')
    folder = f'2021 Census GCP {names[geo]} for {state}'
    with ZipFile(path, 'w') as z:
        z.writestr(f'{folder}/2021Census_G01_{state}_{geo}.csv',
                   f'{geo}_CODE_2021,Tot_P_P\n{code},{token}\n')
        z.writestr(f'{folder}/2021Census_G02_{state}_{geo}.csv',
                   f'{geo}_CODE_2021,Median_age_persons\n{code},32\n')
        for part, column, value in [('G04A', 'Age_yr_0_M', '10'),
                                    ('G04B', 'Age_yr_56_P', '25')]:
            z.writestr(f'{folder}/2021Census_{part}_{state}_{geo}.csv',
                       f'{geo}_CODE_2021,{column}\n{code},{value}\n')
        z.writestr(META, workbook() if metadata is None else metadata)
        z.writestr('Metadata/2021Census_geog_desc_1st_2nd_3rd_release.xlsx', b'ignored')
        z.writestr('Readme/2021AboutDataPacks_readme.txt', 'Synthetic data, real schema')
    return path


def compressed_damage(path, member, kind):
    """Real DEFLATE fixture; corrupt a block type, expected CRC or member method/flags."""
    with ZipFile(path) as z:
        members = {n: z.read(n) for n in z.namelist()}
    with ZipFile(path, 'w', compression=ZIP_DEFLATED) as z:
        for name, content in members.items():
            z.writestr(name, content)
    with ZipFile(path) as z:
        info = z.getinfo(member)
        directory = z.start_dir
    raw = bytearray(path.read_bytes())
    if kind == 'deflate':
        name_size, extra_size = struct.unpack_from('<HH', raw, info.header_offset+26)
        start = info.header_offset+30+name_size+extra_size
        # BTYPE=3 is reserved, forcing a zlib error without breaking the file list.
        raw[start] = (raw[start] & 0xf8) | 7
    elif kind in ('crc', 'encrypted', 'deflate64'):
        if kind == 'crc':
            struct.pack_into('<I', raw, info.header_offset+14, info.CRC ^ 1)
        while raw[directory:directory+4] == b'PK\x01\x02':
            name_size, extra_size, comment_size = struct.unpack_from('<HHH', raw, directory+28)
            name = bytes(raw[directory+46:directory+46+name_size]).decode('utf-8')
            if name == member:
                if kind == 'crc':
                    struct.pack_into('<I', raw, directory+16, info.CRC ^ 1)
                elif kind == 'encrypted':
                    struct.pack_into('<H', raw, directory+8, info.flag_bits | 1)
                else:
                    # Method 9 (Deflate64) is valid ZIP but unsupported by zipfile.
                    struct.pack_into('<H', raw, directory+10, 9)
                break
            directory += 46+name_size+extra_size+comment_size
        else:
            raise AssertionError('Missing central directory record')
    else:
        raise AssertionError('Unknown corruption kind')
    path.write_bytes(raw)
    return path


class DataFeatureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.source = make_pack(self.base/'source.zip')

    def test_unexpected_numeric_tokens_rejected_with_context(self):
        for token in ['12a', '"1,234"', 'unknown', 'NaN', '#N/A', 'inf', '-Infinity', '1e999']:
            with self.subTest(token=token):
                make_pack(self.source, token=token)
                with self.assertRaisesRegex(ValueError, 'Invalid numeric value.*G01.*Tot_P_P.*POA2000'):
                    etl.load_census(source_zip=self.source)

    def test_documented_missing_markers_only(self):
        for token in etl.MISSING:
            with self.subTest(token=token):
                make_pack(self.source, token=f' {token.upper()} ')
                df = etl.load_census(source_zip=self.source)
                self.assertTrue(pd.isna(df['Tot_P_P'].iloc[0]))

    def test_finite_numeric_forms_and_bare_codes_survive(self):
        for token, expected in [('0', 0), ('-3', -3), ('1.25', 1.25), ('1e3', 1000)]:
            make_pack(self.source, state='NT', code='0800', token=token)
            df = etl.load_census(source_zip=self.source, state='NT', codes=[800])
            self.assertEqual(df['POA_CODE_2021'].tolist(), ['0800'])
            self.assertEqual(df['Tot_P_P'].iloc[0], expected)

    def test_bad_numeric_export_does_not_replace_previous_outputs(self):
        out = self.base/'out'
        files = etl.export_census(source_zip=self.source, out=out)
        before = {p.name: p.read_bytes() for p in out.iterdir()}
        make_pack(self.source, token='12a')
        with self.assertRaisesRegex(ValueError, 'Invalid numeric'):
            etl.export_census(source_zip=self.source, out=out)
        self.assertEqual(before, {p.name: p.read_bytes() for p in out.iterdir()})
        self.assertTrue(files['G01'].exists())

    def test_cache_corruption_is_rejected_offline_without_replacement(self):
        content = b'not a zip'
        self.source.write_bytes(content)
        with patch.object(etl.requests, 'get', side_effect=AssertionError('network accessed')):
            with self.assertRaisesRegex(BadZipFile, 'Move it aside'):
                etl.download_if_needed('https://example.invalid/pack.zip', self.source)
        self.assertEqual(self.source.read_bytes(), content)

    def test_reused_and_explicit_zip_checks_do_not_decompress_unselected_members(self):
        damaged = self.source.read_bytes().replace(b'27936', b'27937')
        self.source.write_bytes(damaged)
        with patch.object(ZipFile, 'testzip', side_effect=AssertionError('full scan')):
            with patch.object(etl.requests, 'get', side_effect=AssertionError('network accessed')):
                etl.download_if_needed('https://example.invalid/pack.zip', self.source)
                df = etl.load_census(source_zip=self.source, table='G02')
                self.assertEqual(df['Median_age_persons'].iloc[0], 32)
                with self.assertRaisesRegex(BadZipFile, 'CRC'):
                    etl.load_census(source_zip=self.source, table='G01')
                url = etl.datapack_url('POA', 'NSW')
                cached = self.base/Path(url).name
                cached.write_bytes(damaged)
                with self.assertRaisesRegex(BadZipFile, 'CRC'):
                    etl.load_census(cache_dir=self.base, table='G01')
        self.assertEqual(self.source.read_bytes(), damaged)

    def test_explicit_non_zip_is_refused_with_context(self):
        self.source.write_bytes(b'not a zip')
        with self.assertRaisesRegex(BadZipFile, 'Invalid ZIP.*Move it aside'):
            etl.load_census(source_zip=self.source)

    def test_compressed_table_corruption_names_archive_in_api_and_cli(self):
        for kind in ['deflate', 'crc']:
            for cached in [False, True]:
                with self.subTest(kind=kind, cached=cached):
                    out = self.base/f'out-{kind}-{cached}'
                    source = out/'raw'/Path(etl.datapack_url('POA', 'NT')).name if cached else self.source
                    source.parent.mkdir(parents=True, exist_ok=True)
                    make_pack(source, state='NT', code='POA0800')
                    member = '2021 Census GCP Postal Areas for NT/2021Census_G01_NT_POA.csv'
                    compressed_damage(source, member, kind)
                    before = source.read_bytes()
                    with self.assertRaises(BadZipFile) as caught:
                        etl.load_census(state='NT', source_zip=None if cached else source,
                                        cache_dir=source.parent)
                    self.assertIn(str(source), str(caught.exception))
                    self.assertIn('Move it aside', str(caught.exception))
                    if kind == 'deflate':
                        self.assertIsInstance(caught.exception.__cause__, etl.zlib.error)
                    command = [sys.executable, str(ROOT/'abs_census_etl.py'),
                               '--geo', 'POA', '--state', 'NT', '--out', str(out)]
                    if not cached:
                        command += ['--zip', str(source)]
                    result = subprocess.run(command, capture_output=True, text=True, timeout=30)
                    self.assertEqual(result.returncode, 1, result.stdout+result.stderr)
                    self.assertIn('ERROR: Invalid ZIP', result.stderr)
                    self.assertIn(str(source), result.stderr)
                    self.assertIn('Move it aside and download again', result.stderr)
                    self.assertNotIn('Traceback', result.stderr)
                    self.assertFalse(list(out.glob('*.csv')))
                    self.assertEqual(source.read_bytes(), before)

    def test_encrypted_or_unsupported_members_are_refused_before_reading(self):
        member = '2021 Census GCP Postal Areas for NT/2021Census_G01_NT_POA.csv'
        for kind, cause in [('encrypted', RuntimeError), ('deflate64', NotImplementedError)]:
            for cached in [False, True]:
                with self.subTest(kind=kind, cached=cached):
                    out = self.base/f'header-{kind}-{cached}'
                    source = (out/'raw'/Path(etl.datapack_url('POA', 'NT')).name if cached
                              else self.base/f'header-{kind}.zip')
                    source.parent.mkdir(parents=True, exist_ok=True)
                    make_pack(source, state='NT', code='POA0800')
                    compressed_damage(source, member, kind)
                    before = source.read_bytes()
                    with patch.object(etl, 'load_csv', side_effect=AssertionError('data read')):
                        with self.assertRaises(BadZipFile) as caught:
                            etl.load_census(state='NT', source_zip=None if cached else source,
                                            cache_dir=source.parent)
                    self.assertIn(str(source), str(caught.exception))
                    self.assertIn('Move it aside', str(caught.exception))
                    self.assertIsInstance(caught.exception.__cause__, cause)
                    command = [sys.executable, str(ROOT/'abs_census_etl.py'),
                               '--geo', 'POA', '--state', 'NT', '--out', str(out)]
                    if not cached:
                        command += ['--zip', str(source)]
                    result = subprocess.run(command, capture_output=True, text=True, timeout=30)
                    self.assertEqual(result.returncode, 1, result.stdout+result.stderr)
                    self.assertIn('ERROR: Invalid ZIP', result.stderr)
                    self.assertIn(str(source), result.stderr)
                    self.assertIn('Move it aside and download again', result.stderr)
                    self.assertNotIn('Traceback', result.stderr)
                    if cached:
                        self.assertFalse(list(out.glob('*.csv')))
                    else:
                        self.assertFalse(out.exists())
                    self.assertEqual(source.read_bytes(), before)

    def test_compressed_metadata_corruption_names_archive_for_discovery(self):
        for kind in ['deflate', 'crc']:
            make_pack(self.source)
            compressed_damage(self.source, META, kind)
            for fn in [etl.describe_census, etl.list_census_tables]:
                with self.assertRaises(BadZipFile) as caught:
                    fn(source_zip=self.source)
                self.assertIn(str(self.source), str(caught.exception))
                self.assertIn('Move it aside', str(caught.exception))
            for cached in [False, True]:
                out = self.base/f'metadata-{kind}-{cached}'
                if cached:
                    cache = out/'raw'
                    cache.mkdir(parents=True)
                    source = cache/Path(etl.datapack_url('POA', 'NSW')).name
                    source.write_bytes(self.source.read_bytes())
                else:
                    source = self.source
                for flag in ['--describe', '--list-tables']:
                    command = [sys.executable, str(ROOT/'abs_census_etl.py'),
                               '--geo', 'POA', '--out', str(out), flag]
                    if not cached:
                        command += ['--zip', str(source)]
                    result = subprocess.run(command, capture_output=True, text=True, timeout=30)
                    self.assertEqual(result.returncode, 1)
                    self.assertIn('ERROR: Invalid ZIP', result.stderr)
                    self.assertIn(str(source), result.stderr)
                    self.assertIn('Move it aside', result.stderr)
                    self.assertNotIn('Traceback', result.stderr)

    def test_eof_zip_reads_are_wrapped_and_old_exports_remain_unchanged(self):
        out = self.base/'existing'
        etl.export_census(source_zip=self.source, out=out)
        before = {p.name: p.read_bytes() for p in out.iterdir()}
        with patch.object(etl, 'load_csv', side_effect=EOFError('truncated member')):
            with self.assertRaisesRegex(BadZipFile, 'source.zip.*truncated member.*Move it aside'):
                etl.export_census(source_zip=self.source, out=out)
        self.assertEqual(before, {p.name: p.read_bytes() for p in out.iterdir()})
        with patch.object(ZipFile, 'read', side_effect=EOFError('truncated metadata')):
            with self.assertRaisesRegex(BadZipFile, 'source.zip.*truncated metadata.*Move it aside'):
                etl.describe_census(source_zip=self.source)

    def test_non_zip_explicit_source_does_not_create_fresh_output(self):
        self.source.write_bytes(b'not a ZIP')
        out = self.base/'fresh'
        with self.assertRaisesRegex(BadZipFile, 'source.zip.*Move it aside'):
            etl.export_census(source_zip=self.source, out=out)
        self.assertFalse(out.exists())
        result = subprocess.run([sys.executable, str(ROOT/'abs_census_etl.py'), '--geo', 'POA',
                                 '--zip', str(self.source), '--out', str(out)],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 1)
        self.assertIn(str(self.source), result.stderr)
        self.assertNotIn('Traceback', result.stderr)
        self.assertFalse(out.exists())

    def test_reference_page_without_area_marker_has_saved_html_diagnostic(self):
        check = runpy.run_path(str(ROOT/'tests/check_abs_data.py'))
        class Response:
            content = b'<html><body>Unexpected page</body></html>'
            text = content.decode()
            def raise_for_status(self):
                pass
        directory = self.base/'references'
        with patch.object(etl.requests, 'get', return_value=Response()):
            with self.assertRaisesRegex(RuntimeError, 'Cannot find QuickStats summary.*inspect saved HTML'):
                check['save_reference']((27936, 32, 'POA2000'), directory)
        self.assertEqual((directory/'POA2000.html').read_bytes(), Response.content)

    def test_manifest_hashes_identify_each_export_not_latest_for_all(self):
        out = self.base/'out'
        old_hash = hashlib.sha256(self.source.read_bytes()).hexdigest()
        etl.export_census(source_zip=self.source, out=out)
        make_pack(self.source, token='123')
        new_hash = hashlib.sha256(self.source.read_bytes()).hexdigest()
        etl.export_census(source_zip=self.source, tables=['G02'], out=out)
        manifest = json.loads((out/'2021_GCP_POA_NSW_manifest.json').read_text())
        self.assertEqual(manifest['source_sha256'], new_hash)
        self.assertEqual(manifest['tables']['G01']['source_sha256'], old_hash)
        self.assertEqual(manifest['tables']['G02']['source_sha256'], new_hash)
        self.assertNotEqual(old_hash, new_hash)

    def test_legacy_hash_is_unknown_even_if_source_path_now_exists(self):
        out = self.base/'out'
        etl.export_census(source_zip=self.source, out=out)
        path = out/'2021_GCP_POA_NSW_manifest.json'
        manifest = json.loads(path.read_text())
        manifest.pop('source_sha256')
        manifest['tables']['G01'].pop('source_sha256')
        path.write_text(json.dumps(manifest))
        etl.export_census(source_zip=self.source, tables=['G02'], out=out)
        self.assertIsNone(json.loads(path.read_text())['tables']['G01']['source_sha256'])

    def test_source_modified_during_read_is_refused_before_publication(self):
        original = etl._read_tables_from_zip
        def change_source(**options):
            result = original(**options)
            make_pack(self.source, token='99999')
            return result
        with patch.object(etl, '_read_tables_from_zip', side_effect=change_source):
            with self.assertRaisesRegex(ValueError, 'Source ZIP changed'):
                etl.export_census(source_zip=self.source, out=self.base/'out')
        self.assertFalse(list((self.base/'out').glob('*.csv')))

    @unittest.skipUnless(os.name == 'posix', 'POSIX replacement of open source file')
    def test_hash_and_data_use_same_open_source_when_path_is_swapped(self):
        before = hashlib.sha256(self.source.read_bytes()).hexdigest()
        replacement = make_pack(self.base/'other.zip', token='123')
        original = etl._read_tables_from_zip
        def swap_source(**options):
            replacement.replace(self.source)
            return original(**options)
        out = self.base/'out'
        with patch.object(etl, '_read_tables_from_zip', side_effect=swap_source):
            etl.export_census(source_zip=self.source, out=out)
        manifest = json.loads((out/'2021_GCP_POA_NSW_manifest.json').read_text())
        self.assertEqual(manifest['source_sha256'], before)
        self.assertEqual(pd.read_csv(out/manifest['tables']['G01']['file'])['Tot_P_P'].iloc[0], 27936)

    def test_discovery_shared_and_inline_rich_strings_without_loading_data(self):
        for shared in [True, False]:
            make_pack(self.source, metadata=workbook(shared=shared), token='12a')
            with patch.object(etl, 'load_csv', side_effect=AssertionError('data read')):
                tables = etl.list_census_tables(source_zip=self.source)
                variables = etl.describe_census(source_zip=self.source, tables=['G04'])
            self.assertEqual(tables['table'].tolist(), ['G01', 'G02', 'G04'])
            self.assertEqual(tables.iloc[2]['parts'], 'G04A,G04B')
            self.assertEqual(tables.iloc[0]['population'], 'Persons')
            self.assertEqual(variables['datapack_table'].tolist(), ['G04A', 'G04B'])
            self.assertEqual(variables.iloc[0]['long_name'], 'Age_years_0_Males')

    def test_lettered_profiles_sharing_one_datapack_part_are_retained(self):
        # Genuine G09C contains the tail of G09a (males) and start of G09b (females).
        rows = VARIABLE_ROWS + [
            ['G1641', 'M_Singapore_0_4', 'MALES_Singapore_Age_0_4_years',
             'G09C', 'G09a', 'Age: 0-4 years|MALES'],
            ['G1771', 'F_Afghanistan_0_4', 'FEMALES_Afghanistan_Age_0_4_years',
             'G09C', 'G09b', 'Age: 0-4 years|FEMALES']]
        for shared in [False, True]:
            make_pack(self.source, metadata=workbook(shared=shared,
                tables=TABLE_ROWS + [['G09', 'Country of Birth of Person by Age by Sex', 'Persons']],
                variables=rows))
            with ZipFile(self.source, 'a') as z:
                z.writestr('2021 Census GCP Postal Areas for NSW/2021Census_G09C_NSW_POA.csv',
                           'POA_CODE_2021,M_Singapore_0_4,F_Afghanistan_0_4\nPOA2000,10,5\n')
            all_variables = etl.describe_census(source_zip=self.source)
            self.assertEqual(len(all_variables), 6)
            for requested in [['G09'], ['G09C']]:
                found = etl.describe_census(source_zip=self.source, tables=requested)
                self.assertEqual(found['profile_table'].tolist(), ['G09a', 'G09b'])
                self.assertEqual(found['table'].tolist(), ['G09', 'G09'])
                self.assertEqual(found['datapack_table'].tolist(), ['G09C', 'G09C'])
            stdout = io.StringIO()
            with patch('sys.stdout', stdout):
                etl.main(['--geo', 'POA', '--zip', str(self.source), '--describe',
                          '--search', 'F_Afghanistan'])
            self.assertIn('G09\tG09b\tG09C\tF_Afghanistan_0_4', stdout.getvalue())

    def test_missing_metadata_error_names_only_missing_parts(self):
        make_pack(self.source, metadata=workbook(variables=VARIABLE_ROWS[:-1]))
        with self.assertRaises(ValueError) as caught:
            etl.describe_census(source_zip=self.source)
        self.assertEqual(str(caught.exception), "Metadata missing for table parts: ['G04B']")

    def test_bad_shared_string_index_has_workbook_context(self):
        data = workbook(shared=True)
        for invalid in ['not-an-integer', '-1', '999999']:
            with ZipFile(io.BytesIO(data)) as z:
                members = {n: z.read(n) for n in z.namelist()}
            name = 'xl/worksheets/sheet1.xml'
            sheet = E.fromstring(members[name])
            sheet.find('.//{'+NS+'}v').text = invalid
            members[name] = E.tostring(sheet)
            out = io.BytesIO()
            with ZipFile(out, 'w') as z:
                for name, content in members.items():
                    z.writestr(name, content)
            make_pack(self.source, metadata=out.getvalue())
            with self.assertRaisesRegex(ValueError, 'Invalid ABS metadata workbook'):
                etl.describe_census(source_zip=self.source)

    def test_discovery_literal_search_and_part_selection(self):
        self.assertEqual(etl.list_census_tables(source_zip=self.source, search='MEDIANS')['table'].tolist(), ['G02'])
        result = etl.describe_census(source_zip=self.source, tables=['G04B'], search='persons')
        self.assertEqual(result['variable'].tolist(), ['Age_yr_56_P'])
        self.assertTrue(etl.describe_census(source_zip=self.source, search='.*').empty)
        found = etl.describe_census(source_zip=self.source, search='median_age')
        self.assertEqual(found['table'].tolist(), ['G02'])
        self.assertEqual(found['variable'].tolist(), ['Median_age_persons'])

    def test_missing_ambiguous_or_malformed_workbook_rejected(self):
        for extra in [None, 'duplicate', 'bad_headers', 'bad_xml', 'duplicate_variable']:
            with self.subTest(extra=extra):
                variables = VARIABLE_ROWS + [VARIABLE_ROWS[-1]] if extra == 'duplicate_variable' else None
                data = workbook(tables=[['wrong']] if extra == 'bad_headers' else None,
                                variables=variables)
                make_pack(self.source, metadata=data if extra != 'bad_xml' else b'bad')
                if extra in [None, 'duplicate']:
                    with ZipFile(self.source) as z:
                        members = {n: z.read(n) for n in z.namelist() if n != META or extra == 'duplicate'}
                    if extra == 'duplicate':
                        members['other/'+Path(META).name] = data
                    with ZipFile(self.source, 'w') as z:
                        for name, content in members.items():
                            z.writestr(name, content)
                with self.assertRaises(ValueError):
                    etl.describe_census(source_zip=self.source)

    def test_metadata_wrong_state_and_old_year_refused(self):
        with self.assertRaisesRegex(ValueError, 'state VIC'):
            etl.list_census_tables(source_zip=self.source, state='VIC')
        with ZipFile(self.source) as z:
            members = {n.replace('2021Census_G', '2016Census_G'): z.read(n) for n in z.namelist()}
        with ZipFile(self.source, 'w') as z:
            for name, content in members.items():
                z.writestr(name, content)
        with self.assertRaisesRegex(ValueError, 'Mixed Census years.*2016.*2021'):
            etl.list_census_tables(source_zip=self.source)

    def test_discovery_cli_and_invalid_search_before_source(self):
        stdout = io.StringIO()
        with patch('sys.stdout', stdout):
            self.assertEqual(etl.main(['--geo', 'POA', '--zip', str(self.source),
                                       '--describe', '--search', 'total_persons']), 0)
        self.assertIn('Tot_P_P\tTotal_Persons_Persons', stdout.getvalue())
        stdout = io.StringIO()
        with patch('sys.stdout', stdout):
            etl.main(['--geo', 'POA', '--zip', str(self.source), '--describe', '--search', 'median_age'])
        self.assertIn('G02\tG02\tMedian_age_persons', stdout.getvalue())
        with patch.object(etl, '_resolve_source', side_effect=AssertionError('source accessed')):
            for fn in [etl.list_census_tables, etl.describe_census]:
                with self.assertRaisesRegex(ValueError, 'search'):
                    fn(search=' ')
            with patch('sys.stderr', io.StringIO()), self.assertRaises(SystemExit):
                etl.main(['--geo', 'POA', '--search', 'age'])

    def test_all_supported_geographies_filter_join_export_and_discover(self):
        real_codes = {'POA': 'POA2000', 'SA1': '10201102801', 'SA2': '101021007',
                      'SA3': '10102', 'SA4': '101', 'LGA': 'LGA17200',
                      'SED': 'SED10001', 'CED': 'CED101', 'STE': '1', 'SAL': 'SAL10001'}
        for geo, code in real_codes.items():
            with self.subTest(geo=geo):
                make_pack(self.source, geo=geo, code=code)
                bare = code.removeprefix(geo)
                for value in [bare, f' {geo.lower()}{bare} ', int(bare)]:
                    df = etl.load_census(geo=geo, source_zip=self.source,
                                         table='G04', codes=[value], long=True)
                    self.assertEqual(df[f'{geo}_CODE_2021'].unique().tolist(), [code])
                    self.assertEqual(set(df['value']), {10, 25})
                self.assertEqual(len(etl.list_census_tables(geo=geo, source_zip=self.source)), 3)
                outputs = etl.export_census(geo=geo, source_zip=self.source, out=self.base/geo)
                self.assertTrue(outputs['G01'].is_file())
                with self.assertRaisesRegex(ValueError, 'Unmatched'):
                    etl.load_census(geo=geo, source_zip=self.source, codes=[bare, '9999' if geo == 'POA' else '99999999999'])

    def test_special_geography_codes_and_aust_state_selection(self):
        for geo, code in [('SA2', '199999499'), ('SA3', '19999'), ('LGA', 'LGA19499'),
                          ('SAL', 'SAL19797'), ('SED', 'SED19494'), ('CED', 'CED194')]:
            make_pack(self.source, geo=geo, state='AUST', code=code)
            df = etl.load_census(geo=geo, state='AUS', source_zip=self.source, codes=[code])
            self.assertEqual(df.iloc[0, 0], code)
            with self.assertRaisesRegex(ValueError, 'state NSW'):
                etl.load_census(geo=geo, state='NSW', source_zip=self.source)

    def test_missing_explicit_source_does_not_create_output(self):
        for source in [self.base/'typo.zip', self.base]:
            out = self.base/'fresh'
            with self.assertRaisesRegex(FileNotFoundError, 'Source ZIP is not a file'):
                etl.export_census(source_zip=source, out=out)
            self.assertFalse(out.exists())
        stderr = io.StringIO()
        with patch('sys.stderr', stderr):
            self.assertEqual(etl.cli(['--geo', 'POA', '--zip', str(self.base/'typo.zip'),
                                      '--out', str(out)]), 1)
        self.assertFalse(out.exists())

    def test_existing_read_only_lock_is_usable_without_replacing_it(self):
        out = self.base/'out'
        out.mkdir()
        lock = out/'.abs-census-etl.lock'
        lock.write_bytes(b'')
        lock.chmod(0o444)
        before = lock.stat()
        try:
            etl.export_census(source_zip=self.source, out=out)
            etl.export_census(source_zip=self.source, tables=['G02'], out=out)
            self.assertEqual(lock.stat().st_ino, before.st_ino)
            self.assertEqual(lock.read_bytes(), b'')
            if os.name == 'posix':
                self.assertEqual(lock.stat().st_mode & 0o777, 0o444)
        finally:
            lock.chmod(0o644)

    def test_lock_support_and_permission_failures_are_not_reported_busy(self):
        lock_module = __import__('msvcrt' if os.name == 'nt' else 'fcntl')
        operation = 'locking' if os.name == 'nt' else 'flock'
        out = self.base/'out'
        out.mkdir()
        unsupported = [errno.ENOLCK, errno.ENOSYS]
        if os.name != 'nt':
            unsupported.append(errno.EACCES)
        for number in unsupported:
            with patch.object(lock_module, operation, side_effect=OSError(number, 'simulated')):
                with self.assertRaises(OSError) as caught:
                    with etl._export_lock(out):
                        self.fail('Unavailable lock acquired')
            self.assertEqual(caught.exception.errno, number)
            self.assertNotIsInstance(caught.exception, BlockingIOError,
                                     'unsupported locks were reported as busy')
            self.assertIn('use a local output folder', str(caught.exception))
        busy = [errno.EAGAIN] + ([errno.EACCES] if os.name == 'nt' else [])
        for number in busy:
            with patch.object(lock_module, operation, side_effect=OSError(number, 'busy')):
                with self.assertRaisesRegex(BlockingIOError, 'Output folder is busy'):
                    with etl._export_lock(out):
                        self.fail('Busy lock acquired')

    def test_lock_contention_and_process_death_release(self):
        out = self.base/'out'
        etl.export_census(source_zip=self.source, out=out)
        # A real independent process holds the lock until terminated, no timed sleep.
        script = ('import sys; from pathlib import Path; '
                  'from abs_census_etl import _export_lock; '
                  'ctx=_export_lock(Path(sys.argv[1])); ctx.__enter__(); '
                  'print("LOCKED",flush=True); sys.stdin.readline()')
        child = subprocess.Popen([sys.executable, '-c', script, str(out)], cwd=ROOT,
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True)
        try:
            self.assertEqual(child.stdout.readline().strip(), 'LOCKED')
            with patch.object(etl, '_resolve_source', side_effect=AssertionError('source accessed')):
                with self.assertRaisesRegex(BlockingIOError, 'busy'):
                    etl.export_census(source_zip=self.source, tables=['G02'], out=out)
        finally:
            child.kill()
            child.communicate(timeout=15)
        etl.export_census(source_zip=self.source, tables=['G02'], out=out)
        manifest = json.loads((out/'2021_GCP_POA_NSW_manifest.json').read_text())
        self.assertEqual(set(manifest['tables']), {'G01', 'G02'})
        self.assertTrue((out/'.abs-census-etl.lock').exists())

    def test_overlapping_exporter_is_refused_while_first_reads_and_preserves_entries(self):
        out = self.base/'out'
        script = '''import sys
import abs_census_etl as etl
original = etl._read_tables_from_zip
def pause(**options):
    print('READING', flush=True)
    sys.stdin.readline()
    return original(**options)
etl._read_tables_from_zip = pause
etl.export_census(source_zip=sys.argv[1],out=sys.argv[2])
'''
        child = subprocess.Popen([sys.executable, '-c', script, str(self.source), str(out)],
                                 cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True)
        try:
            self.assertEqual(child.stdout.readline().strip(), 'READING')
            with self.assertRaisesRegex(BlockingIOError, 'busy'):
                etl.export_census(source_zip=self.source, tables=['G02'], out=out)
            child.communicate(input='continue\n', timeout=30)
            self.assertEqual(child.returncode, 0)
        finally:
            if child.poll() is None:
                child.kill()
                child.communicate(timeout=15)
        etl.export_census(source_zip=self.source, tables=['G02'], out=out)
        manifest = json.loads((out/'2021_GCP_POA_NSW_manifest.json').read_text())
        self.assertEqual(set(manifest['tables']), {'G01', 'G02'})

    def test_lock_releases_after_source_failure(self):
        out = self.base/'out'
        with patch.object(etl, '_resolve_source', side_effect=ValueError('probe')):
            with self.assertRaisesRegex(ValueError, 'probe'):
                etl.export_census(out=out)
        etl.export_census(source_zip=self.source, out=out)


if __name__ == '__main__':
    unittest.main()
