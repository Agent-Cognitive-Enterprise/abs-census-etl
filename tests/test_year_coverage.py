"""Offline two-vintage POA contracts; small subsets of observed GCP layouts."""
from contextlib import ExitStack
from pathlib import Path
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
import warnings
from unittest.mock import patch
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import abs_census_etl as etl
try:
    from tests.test_data_features import workbook, TABLE_ROWS, VARIABLE_ROWS
except ModuleNotFoundError:
    from test_data_features import workbook, TABLE_ROWS, VARIABLE_ROWS


def year_pack(path, *, year=2016, state='NSW', bare=False):
    """2016 subset omits G02 deliberately; genuine full-pack coverage is separate."""
    codes = {'NSW': ['POA2000', 'POA2010'], 'NT': ['POA0800', 'POA0810'],
             'OT': ['POA2899', 'POA9494', 'POA9797'],
             'VIC': ['POA3000', 'POA3004'], 'QLD': ['POA4000', 'POA4005'],
             'SA': ['POA5000', 'POA5006'], 'WA': ['POA6000', 'POA6005'],
             'TAS': ['POA7000', 'POA7004'], 'ACT': ['POA2600', 'POA2601'],
             'AUS': ['POA2000', 'POA2010']}[state]
    if bare:
        codes = [code.removeprefix('POA') for code in codes]
    code_column = f'POA_CODE_{year}'
    rows = TABLE_ROWS if year == 2021 else [r for r in TABLE_ROWS if not r or r[0] != 'G02']
    variables = VARIABLE_ROWS if year == 2021 else [r for r in VARIABLE_ROWS
                                                   if len(r) < 4 or r[3] != 'G02']
    content = workbook(tables=rows, variables=variables)
    if year == 2016:
        source = ZipFile(io.BytesIO(content))
        output = io.BytesIO()
        with source, ZipFile(output, 'w') as adjusted:
            for name in source.namelist():
                data = source.read(name).replace(b'2021', b'2016')
                if name == 'xl/workbook.xml':
                    data = data.replace(b'Table Number, Name, Population',
                                        b'Table number, name, population').replace(
                                            b'Cell Descriptors Information',
                                            b'Cell descriptors information')
                adjusted.writestr(name, data)
        content = output.getvalue()
    # Real 2016 national CSV uses AUS despite an enclosing AUST folder.
    token = 'AUST' if state == 'AUS' and year == 2021 else state
    folder_token = 'AUST' if state == 'AUS' else state
    folder = f'{year} Census GCP Postal Areas for {folder_token}'
    with ZipFile(path, 'w', compression=ZIP_DEFLATED) as archive:
        measures = [('G01', 'Tot_P_P', '27936' if year == 2021 else '27411'),
                    ('G04A', 'Age_yr_0_M', '10'), ('G04B', 'Age_yr_56_P', '25')]
        if year == 2021:
            measures.append(('G02', 'Median_age_persons', '32'))
        for part, column, value in measures:
            archive.writestr(f'{folder}/{year}Census_{part}_{token}_POA.csv',
                             code_column + ',' + column + '\n'
                             + ''.join(f'{code},{value}\n' for code in codes))
        archive.writestr(f'Metadata/Metadata_{year}_GCP_DataPack.xlsx', content)
        geography = ('2016Census_geog_desc_1st_and_2nd_release.xlsx' if year == 2016
                     else '2021Census_geog_desc_1st_2nd_3rd_release.xlsx')
        archive.writestr('Metadata/' + geography, b'ignored, named from real archive')
        archive.writestr(f'Metadata/{year}_GCP_Sequential_Template.xlsx', b'ignored')
        archive.writestr('Readme/AboutDatpacks_readme.txt', 'Synthetic subset; real schema')
    return path


class Response:
    """Offline stand-in for one streamed requests.get response."""
    def __init__(self, body):
        self.body = body
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass
    def raise_for_status(self):
        pass
    def iter_content(self, **kwargs):
        yield self.body


class YearCoverageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.old = year_pack(self.root / '2016.zip')
        self.new = year_pack(self.root / '2021.zip', year=2021)

    def test_explicit_year_changes_source_header_values_and_inventory(self):
        old = etl.load_census(year=2016, source_zip=self.old, codes=['2000'])
        new = etl.load_census(year=2021, source_zip=self.new, codes=['2000'])
        self.assertEqual(old.columns.tolist(), ['POA_CODE_2016', 'Tot_P_P'])
        self.assertEqual(old['Tot_P_P'].tolist(), [27411])
        self.assertEqual(new['Tot_P_P'].tolist(), [27936])
        pd.testing.assert_frame_equal(new, etl.load_census(source_zip=self.new, codes=['2000']))
        self.assertEqual(etl.list_census_tables(year=2016, source_zip=self.old)['table'].tolist(),
                         ['G01', 'G04'])
        self.assertEqual(etl.list_census_tables(source_zip=self.new)['table'].tolist(),
                         ['G01', 'G02', 'G04'])

    def test_2016_keep_and_integral_year_contract(self):
        import numpy as np
        frame = etl.load_census(year=np.int64(2016), source_zip=self.old,
                                 codes=[' poa2000 '], keep=[' Tot_P_P '], long=True)
        self.assertEqual(frame.columns.tolist(), ['POA_CODE_2016', 'variable', 'value'])
        self.assertEqual(frame.iloc[0].tolist(), ['POA2000', 'Tot_P_P', 27411])
        with self.assertRaisesRegex(ValueError, 'Unknown column'):
            etl.load_census(year=2016, source_zip=self.old, keep=['not_a_measure'])

    def test_sheet_case_normalization_still_rejects_duplicate_required_sheets(self):
        from xml.etree import ElementTree as E
        content = workbook()
        output = io.BytesIO()
        with ZipFile(io.BytesIO(content)) as original, ZipFile(output, 'w') as altered:
            for name in original.namelist():
                body = original.read(name)
                if name == 'xl/workbook.xml':
                    document = E.fromstring(body)
                    sheets = next(child for child in document if child.tag.endswith('}sheets'))
                    duplicate = E.fromstring(E.tostring(sheets[0]))
                    duplicate.set('name', 'Table number, name, population')
                    sheets.append(duplicate)
                    body = E.tostring(document)
                altered.writestr(name, body)
        with self.assertRaisesRegex(ValueError, 'Duplicate metadata sheet'):
            etl._xlsx_sheets(output.getvalue())

    def test_2016_default_description_uses_actual_sheet_case_and_all_parts(self):
        frame = etl.describe_census(year=2016, source_zip=self.old)
        self.assertEqual(set(frame['datapack_table']), {'G01', 'G04A', 'G04B'})
        self.assertEqual(frame['variable'].tolist(), ['Tot_P_P', 'Age_yr_0_M', 'Age_yr_56_P'])
        self.assertEqual(etl.describe_census(year=2016, source_zip=self.old,
                                           search=' total_persons ')['variable'].tolist(), ['Tot_P_P'])
        self.assertEqual(len(etl.describe_census(year=2016, source_zip=self.old,
                                               tables=['g04b'])), 1)

    def test_2016_bare_nt_codes_padding_case_whitespace_and_long_shape(self):
        source = year_pack(self.root / 'nt.zip', state='NT', bare=True)
        for code in [' 0800 ', ' poa0800 ', 800, 800.0]:
            with self.subTest(code=code):
                frame = etl.load_census(year=2016, state='NT', source_zip=source,
                                         table='G04', codes=[code], long=True)
                self.assertEqual(frame.columns.tolist(), ['POA_CODE_2016', 'variable', 'value'])
                self.assertEqual(frame['POA_CODE_2016'].tolist(), ['0800', '0800'])
                self.assertEqual(set(frame['value']), {10, 25})
        self.assertEqual(etl.load_census(year=2016, state='NT', source_zip=source,
                                         codes=['0810']).iloc[0, 0], '0810')
        for codes in [['0800', '0899'], ['9999']]:
            with self.assertRaisesRegex(ValueError, 'Unmatched POA'):
                etl.load_census(year=2016, state='NT', source_zip=source, codes=codes)

    def test_2016_ot_special_codes_and_wrong_state_are_explicit(self):
        source = year_pack(self.root / 'ot.zip', state='OT')
        frame = etl.load_census(year=2016, state='OT', source_zip=source,
                                 codes=['2899', '9494', '9797'])
        self.assertEqual(frame['POA_CODE_2016'].tolist(), ['POA2899', 'POA9494', 'POA9797'])
        for code in ['9494', '9797', '2899']:
            with self.assertRaisesRegex(ValueError, 'Unmatched POA'):
                etl.load_census(year=2016, source_zip=self.old, codes=[code])
        with self.assertRaisesRegex(ValueError, 'state NSW'):
            etl.load_census(year=2016, source_zip=source)

    def test_all_states_and_national_tokens_for_both_years(self):
        for year in [2016, 2021]:
            for state in sorted(etl.VALID_STATES):
                with self.subTest(year=year, state=state):
                    source = year_pack(self.root / 'state.zip', year=year, state=state)
                    loaded = etl.load_census(year=year, state=state.lower(), source_zip=source)
                    self.assertEqual(loaded.columns[0], f'POA_CODE_{year}')
                    self.assertTrue(len(etl.list_census_tables(year=year, state=state, source_zip=source)))
                    self.assertTrue(len(etl.describe_census(year=year, state=state, source_zip=source)))
        self.assertEqual(etl.datapack_url(' poa ', ' nsw ', year=2016).split('/')[-1],
                         '2016_GCP_POA_for_NSW_short-header.zip')
        self.assertEqual(etl.datapack_url('POA', 'NSW'), etl.datapack_url('POA', 'NSW', year=2021))

    def test_year_and_geography_validation_precedes_every_source_access(self):
        functions = [etl.load_census, etl.load_census_tables, etl.list_census_tables,
                     etl.describe_census, etl.export_census]
        with patch.object(etl, '_resolve_source', side_effect=AssertionError('source accessed')):
            for fn in functions:
                for year in [True, False, 2016.0, '2016', None, 2011, 2026]:
                    with self.subTest(function=fn.__name__, year=year):
                        with self.assertRaisesRegex(ValueError, 'Unsupported Census year'):
                            fn(year=year)
                for geo in ['SA1', 'SA2', 'SAL', 'SSC']:
                    with self.assertRaisesRegex(ValueError, 'Unsupported geography'):
                        fn(year=2016, geo=geo)
                with self.assertRaisesRegex(ValueError, 'Unsupported geography'):
                    fn(year=2021, geo='SSC')

    def test_direct_helpers_require_and_propagate_the_selected_year(self):
        with ZipFile(self.old) as archive:
            self.assertEqual(len(etl.find_members(archive, 'G04', 'poa', 'nsw', year=2016)), 2)
            self.assertEqual(etl.combine_table(archive, 'G04', 'poa', year=2016).columns[0],
                             'POA_CODE_2016')
            with self.assertRaisesRegex(ValueError, 'Wrong Census year'):
                etl.find_members(archive, 'G01', 'POA')
        self.assertIsNone(etl.MEMBER_RE.fullmatch('2016Census_G01_NSW_POA.csv'))
        self.assertIn('SAL', etl.VALID_GEOS)
        self.assertNotIn('SSC', etl.VALID_GEOS)

    def test_wrong_year_and_mixed_year_packs_are_not_silently_filtered(self):
        functions = [etl.load_census, etl.load_census_tables,
                     etl.list_census_tables, etl.describe_census]
        for fn in functions:
            with self.subTest(function=fn.__name__):
                with self.assertRaisesRegex(ValueError, 'requested 2021 GCP; found 2016.*--year 2016'):
                    fn(source_zip=self.old)
                with self.assertRaisesRegex(ValueError, 'requested 2016 GCP; found 2021.*--year 2021'):
                    fn(year=2016, source_zip=self.new)
        with ZipFile(self.new, 'a') as archive:
            archive.writestr('2016Census_G01_NSW_POA.csv', 'POA_CODE_2016,Tot_P_P\n2000,1\n')
        for fn in functions:
            with self.assertRaisesRegex(ValueError, 'Mixed Census years.*2016.*2021'):
                fn(source_zip=self.new)

    def test_non_gcp_and_unidentified_pack_diagnostics_are_specific(self):
        for profile, part, geo in [('TSP', 'T01', 'SA2'), ('IP', 'I01A', 'LGA'),
                                   ('WPP', 'W01', 'POA'), ('PEP', 'P01', 'POA')]:
            source = self.root / f'{profile}.zip'
            with ZipFile(source, 'w') as archive:
                archive.writestr(f'2021Census_{part}_NSW_{geo}.csv',
                                 f'{geo}_CODE_2021,value\n{"101021007" if geo == "SA2" else "LGA17200"},1\n')
                # TSP/IP follow slice 11 names. W/P are synthetic family probes;
                # this does not assert a genuine workbook/member layout for them.
                archive.writestr(f'Metadata/Metadata_2021_{profile}_DataPack_R1_R2.xlsx', b'not read')
            for fn in [etl.load_census, etl.list_census_tables, etl.describe_census]:
                with self.assertRaisesRegex(ValueError, f'Wrong DataPack profile.*{profile}'):
                    fn(source_zip=source)
        source = self.root / 'unidentified.zip'
        with ZipFile(source, 'w') as archive:
            archive.writestr('synthetic-unidentified.csv', 'x,y\n')
        with self.assertRaisesRegex(ValueError, 'Unsupported/unidentified DataPack'):
            etl.describe_census(source_zip=source)

    def test_wrong_year_non_gcp_does_not_recommend_an_unsupported_profile(self):
        source = self.root / 'tsp-other-year.zip'
        with ZipFile(source, 'w') as archive:
            archive.writestr('2021Census_T01_NSW_SA2.csv',
                             'SA2_CODE_2021,Tot_persons_C16_P\n101021007,1\n')
        with self.assertRaises(ValueError) as caught:
            etl.describe_census(year=2016, source_zip=source)
        self.assertIn('found 2021 profiles', str(caught.exception))
        self.assertIn('TSP', str(caught.exception))
        self.assertNotIn('--year 2021', str(caught.exception))

    def test_wrong_year_profile_and_mixed_sources_do_not_create_export_folder(self):
        bad = [self.old]
        with ZipFile(self.new, 'a') as archive:
            archive.writestr('2016Census_G01_NSW_POA.csv', 'POA_CODE_2016,Tot_P_P\n2000,1\n')
        bad.append(self.new)
        profile = self.root / 'tsp.zip'
        with ZipFile(profile, 'w') as archive:
            archive.writestr('2021Census_T01_NSW_SA2.csv', 'SA2_CODE_2021,x\n101021007,1\n')
        bad.append(profile)
        for index, source in enumerate(bad):
            out = self.root / f'fresh-{index}'
            with self.assertRaises(ValueError):
                etl.export_census(source_zip=source, out=out)
            self.assertFalse(out.exists())
            out.mkdir()
            with self.assertRaises(ValueError):
                etl.export_census(source_zip=source, out=out)
            self.assertEqual(list(out.iterdir()), [])
            for destination in [self.root / f'cli-fresh-{index}', out]:
                stderr = io.StringIO()
                with patch('sys.stderr', stderr):
                    status = etl.cli(['--geo', 'POA', '--zip', str(source),
                                      '--out', str(destination)])
                self.assertEqual(status, 1)
                self.assertIn('ERROR:', stderr.getvalue())
                self.assertTrue(not destination.exists() or not list(destination.iterdir()))

    def test_2016_export_is_refused_before_source_or_output(self):
        out = self.root / 'not-created'
        with patch.object(etl, '_resolve_source', side_effect=AssertionError('source accessed')):
            with self.assertRaisesRegex(ValueError, '2016 exports are not enabled'):
                etl.export_census(year=2016, out=out)
        self.assertFalse(out.exists())

    def test_cli_discovers_2016_but_does_not_export_it(self):
        for flags, expected in [(['--list-tables'], 'G04A,G04B'),
                                (['--describe', '--search', 'total_persons'], 'Tot_P_P'),
                                (['--list-columns'], 'Tot_P_P')]:
            result = subprocess.run([sys.executable, str(ROOT / 'abs_census_etl.py'),
                '--geo', 'POA', '--year', '2016', '--zip', str(self.old), *flags],
                capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(expected, result.stdout)
            if flags == ['--list-columns']:
                self.assertNotIn('POA_CODE_2016', result.stdout, 'code column leaked')
                self.assertNotIn('POA_CODE_2021', result.stdout, 'code column leaked')
        stderr = io.StringIO()
        out = self.root / 'cli-export'
        with patch('sys.stderr', stderr):
            status = etl.cli(['--geo', 'POA', '--year', '2016', '--out', str(out)])
        self.assertEqual(status, 1)
        self.assertIn('ERROR: 2016 exports are not enabled', stderr.getvalue())
        self.assertFalse(out.exists())

    def test_cache_paths_do_not_reuse_other_year_and_have_identity_checks(self):
        cache = self.root / 'cache'
        cache.mkdir()
        (cache / '2016_GCP_POA_for_NSW_short-header.zip').write_bytes(self.old.read_bytes())
        (cache / '2021_GCP_POA_for_NSW_short-header.zip').write_bytes(self.new.read_bytes())
        with patch.object(etl.requests, 'get', side_effect=AssertionError('network')):
            self.assertEqual(etl.load_census(year=2016, cache_dir=cache).iloc[0, 1], 27411)
            self.assertEqual(etl.load_census(cache_dir=cache).iloc[0, 1], 27936)
            (cache / '2016_GCP_POA_for_NSW_short-header.zip').write_bytes(self.new.read_bytes())
            with self.assertRaisesRegex(ValueError, 'Wrong Census year'):
                etl.load_census(year=2016, cache_dir=cache)

    def test_2016_split_and_numeric_integrity_checks_remain_strict(self):
        for table, column, token, message in [('G01', 'Tot_P_P', '12a', 'Invalid numeric value'),
                                             ('G04B', 'Age_yr_56_P', '25', 'different POA_CODE_2016')]:
            source = self.root / 'broken.zip'
            year_pack(source)
            with ZipFile(source) as archive:
                members = {name: archive.read(name) for name in archive.namelist()}
            name = next(name for name in members if f'_{table}_NSW_POA.csv' in name)
            members[name] = f'POA_CODE_2016,{column}\nPOA2000,{token}\n'.encode()
            with ZipFile(source, 'w', compression=ZIP_DEFLATED) as archive:
                for name, body in members.items():
                    archive.writestr(name, body)
            with self.assertRaisesRegex(ValueError, message):
                etl.load_census(year=2016, table=table[:3], source_zip=source)

    def test_cached_identity_is_checked_at_resolution_with_recovery_and_path(self):
        cache = self.root / 'cache'
        cache.mkdir()
        cached = cache / '2016_GCP_POA_for_NSW_short-header.zip'
        original = self.new.read_bytes()
        cached.write_bytes(original)
        with patch.object(etl.requests, 'get', side_effect=AssertionError('network')):
            with self.assertRaises(ValueError) as caught:
                etl._resolve_source(geo='POA', state='NSW', source_zip=None,
                                    cache_dir=cache, year=2016)
        message = str(caught.exception)
        self.assertIn(str(cached), message)
        self.assertIn('Move it aside', message)
        self.assertEqual(message.count(str(cached)), 1)
        self.assertNotIn('..', message)
        self.assertNotIn('.part', message)
        self.assertNotIn('--year 2021', message)
        self.assertEqual(cached.read_bytes(), original)

    def test_wrong_download_identity_never_enters_cache_and_retry_succeeds(self):
        profile = self.root / 'profile.zip'
        with ZipFile(profile, 'w', compression=ZIP_DEFLATED) as archive:
            archive.writestr('2016Census_T01_NSW_POA.csv', 'POA_CODE_2016,x\n2000,1\n')
        for invalid in [self.new, profile]:
            cache = self.root / invalid.stem / 'cache'
            with patch.object(etl.requests, 'get', return_value=Response(invalid.read_bytes())):
                with self.assertRaisesRegex(ValueError, 'No cache entry was published') as caught:
                    etl._resolve_source(geo='POA', state='NSW', source_zip=None,
                                        cache_dir=cache, year=2016)
            message = str(caught.exception)
            destination = cache / '2016_GCP_POA_for_NSW_short-header.zip'
            self.assertEqual(message.count(str(destination)), 1)
            self.assertNotIn('.part', message)
            self.assertNotIn('..', message)
            self.assertIn('Retry the download', message)
            self.assertIn(etl.datapack_url('POA', 'NSW', year=2016), message)
            self.assertEqual(list(cache.iterdir()), [])
            with patch.object(etl.requests, 'get', return_value=Response(self.old.read_bytes())):
                source = etl._resolve_source(geo='POA', state='NSW', source_zip=None,
                                             cache_dir=cache, year=2016)
            self.assertEqual(source.read_bytes(), self.old.read_bytes())
            self.assertEqual(list(cache.iterdir()), [source])

    def test_corrupt_download_names_cache_path_not_deleted_temporary_file(self):
        cache = self.root / 'corrupt' / 'cache'
        url = etl.datapack_url('POA', 'NSW', year=2016)
        for body in [b'<html>Service unavailable</html>', self.old.read_bytes()[:-200]]:
            with self.subTest(size=len(body)):
                with patch.object(etl.requests, 'get', return_value=Response(body)):
                    with self.assertRaises(BadZipFile) as caught:
                        etl._resolve_source(geo='POA', state='NSW', source_zip=None,
                                            cache_dir=cache, year=2016)
                message = str(caught.exception)
                self.assertNotIn('.part', message, 'temporary path named')
                destination = cache / Path(url).name
                self.assertEqual(message.count(str(destination)), 1)
                self.assertIn('invalid ZIP', message)
                self.assertIn('No cache entry was published', message)
                self.assertNotIn('Move it aside', message)
                self.assertIn(url, message)
                self.assertEqual(list(cache.iterdir()), [])
        stderr = io.StringIO()
        with patch.object(etl.requests, 'get', return_value=Response(b'not a zip')):
            with patch('sys.stderr', stderr), patch('sys.stdout', io.StringIO()):
                self.assertEqual(etl.cli(['--geo', 'POA', '--year', '2016', '--list-tables',
                                          '--out', str(self.root / 'corrupt-cli')]), 1)
        self.assertIn('ERROR: Downloaded DataPack rejected for', stderr.getvalue())
        self.assertNotIn('.part', stderr.getvalue())

    def test_explicit_resolution_identity_error_includes_source_path(self):
        with self.assertRaises(ValueError) as caught:
            etl._resolve_source(geo='POA', state='NSW', source_zip=self.old,
                                cache_dir=self.root, year=2021)
        self.assertIn(str(self.old), str(caught.exception), 'source path missing')

    def test_unsupported_other_year_geography_does_not_suggest_that_year(self):
        source = self.root / '2016-sa1.zip'
        with ZipFile(source, 'w') as archive:
            archive.writestr('2016Census_G01_NSW_SA1.csv',
                             'SA1_7DIGITCODE_2016,Tot_P_P\n1100701,1\n')
        with self.assertRaises(ValueError) as caught:
            etl.load_census(geo='SA1', source_zip=source)
        self.assertIn('found 2016', str(caught.exception))
        self.assertIn("2016 supports only ['POA']", str(caught.exception))
        self.assertNotIn('--year 2016', str(caught.exception))

    def test_2016_national_member_token_is_not_2021_aust(self):
        source = year_pack(self.root / 'national.zip', state='AUS')
        with ZipFile(source) as archive:
            members = {name.replace('_AUS_POA.csv', '_AUST_POA.csv'): archive.read(name)
                       for name in archive.namelist()}
        with ZipFile(source, 'w') as archive:
            for name, body in members.items():
                archive.writestr(name, body)
        with self.assertRaisesRegex(ValueError, 'Invalid national member token'):
            etl.load_census(year=2016, state='AUS', source_zip=source)

    def test_vintage_configuration_is_private_and_recursively_read_only(self):
        self.assertFalse(hasattr(etl, 'VINTAGES'))
        with self.assertRaises(TypeError):
            etl._VINTAGES[2016] = {}
        with self.assertRaises(TypeError):
            etl._VINTAGES[2016]['columns']['SA1'] = 'SA1_7DIGITCODE_2016'
        with self.assertRaises(TypeError):
            etl._VINTAGES[2016]['national_tokens'] = frozenset({'AUST'})

    def test_discovery_checks_identity_per_open_operation_not_per_table(self):
        with patch.object(etl, '_check_archive', wraps=etl._check_archive) as check:
            etl.list_census_tables(year=2016, source_zip=self.old)
        self.assertEqual(check.call_count, 2)  # Resolution and the actual reader.
        with patch.object(etl, '_check_archive', wraps=etl._check_archive) as check:
            etl.describe_census(year=2016, source_zip=self.old)
        self.assertEqual(check.call_count, 2)
        with ZipFile(self.old) as archive:
            with patch.object(etl, '_check_archive', wraps=etl._check_archive) as check:
                etl.combine_table(archive, 'G04', 'POA', 'NSW', year=2016)
            self.assertEqual(check.call_count, 1)

    def test_explicit_format_preflight_precedes_zip_access_and_is_repeated_under_lock(self):
        out = self.root / 'out'
        out.mkdir()
        conflict = out / '2021_GCP_POA_NSW_G01_wide.parquet'
        conflict.write_bytes(b'existing')
        with patch.object(etl, '_validate_zip', side_effect=AssertionError('source accessed')):
            with self.assertRaises(FileExistsError):
                etl.export_census(source_zip=self.old, out=out)
        self.assertEqual(list(out.iterdir()), [conflict])
        conflict.unlink()
        validate = etl._validate_source_identity
        def concurrent_export(*args, **kwargs):
            validate(*args, **kwargs)
            conflict.write_bytes(b'published by another export')
        with patch.object(etl, '_validate_source_identity', side_effect=concurrent_export):
            with patch.object(etl, '_resolve_source', side_effect=AssertionError('source accessed')):
                with self.assertRaises(FileExistsError):
                    etl.export_census(source_zip=self.new, out=out)
        self.assertEqual(conflict.read_bytes(), b'published by another export')

    def test_destination_refusals_precede_every_explicit_zip_access(self):
        cases = [('journal', etl._ExportRecoveryError, 'Unresolved export'),
                 ('symlink', ValueError, 'Symlinked export'),
                 ('directory', IsADirectoryError, 'not a regular file'),
                 ('invalid-manifest', ValueError, 'Invalid existing manifest'),
                 ('incompatible-manifest', ValueError, 'Incompatible existing manifest'),
                 ('read-only', PermissionError, 'Read-only export output')]
        # An existing lock file is taken first instead; refusals still precede the ZIP.
        variants = [(source, locked) for source in [self.old, self.new] for locked in [False, True]]
        for kind, exception, message in cases:
            for source, locked in variants:
                with self.subTest(kind=kind, source=source.name, lock_file=locked):
                    out = self.root / f'{kind}-{source.stem}-{"locked" if locked else "unlocked"}'
                    out.mkdir()
                    if locked:
                        (out / '.abs-census-etl.lock').touch()
                    data = out / '2021_GCP_POA_NSW_G01_wide.csv'
                    manifest = out / '2021_GCP_POA_NSW_manifest.json'
                    if kind == 'journal':
                        folder = out / '.abs-export-incomplete'
                        folder.mkdir()
                        (folder / 'recovery.json').write_text('{')
                    elif kind == 'symlink':
                        try:
                            data.symlink_to(self.new)
                        except OSError:
                            continue  # Native Windows may not permit test symlinks.
                    elif kind == 'directory':
                        data.mkdir()
                    elif kind == 'invalid-manifest':
                        manifest.write_text('{')
                    elif kind == 'incompatible-manifest':
                        manifest.write_text('{}')
                    else:
                        data.write_text('existing')
                    before = sorted(str(p.relative_to(out)) for p in out.rglob('*'))
                    with ExitStack() as stack:
                        for name in ['ZipFile', '_validate_zip', '_validate_source_identity',
                                     '_resolve_source'] + ([] if locked else ['_export_lock']):
                            stack.enter_context(patch.object(etl, name,
                                side_effect=AssertionError('source or lock accessed before refusal')))
                        if kind == 'read-only':
                            stack.enter_context(patch.object(etl.os, 'access',
                                side_effect=lambda path, flags: Path(path) != data))
                        with self.assertRaisesRegex(exception, message):
                            etl.export_census(source_zip=source, out=out)
                    self.assertEqual(before, sorted(str(p.relative_to(out)) for p in out.rglob('*')))

    def test_destination_journal_is_rechecked_under_lock_after_explicit_validation(self):
        out = self.root / 'journal-race'
        out.mkdir()
        validate = etl._validate_source_identity
        def introduce_journal(*args, **kwargs):
            validate(*args, **kwargs)
            folder = out / '.abs-export-concurrent'
            folder.mkdir()
            (folder / 'recovery.json').write_text('{}')
        with patch.object(etl, '_validate_source_identity', side_effect=introduce_journal):
            with patch.object(etl, '_resolve_source', side_effect=AssertionError('source resolved')):
                with self.assertRaisesRegex(etl._ExportRecoveryError, 'Unresolved export'):
                    etl.export_census(source_zip=self.new, out=out)
        self.assertFalse(list(out.glob('*.csv')))

    def test_locked_manifest_reread_preserves_export_completed_after_early_check(self):
        seed = self.root / 'manifest-seed'
        etl.export_census(source_zip=self.new, out=seed)
        # Copied outputs have no lock file, so the explicit ZIP is checked before locking.
        out = self.root / 'manifest-race'
        shutil.copytree(seed, out, ignore=shutil.ignore_patterns('.abs-census-etl.lock'))
        validate = etl._validate_source_identity
        published = False
        def another_completed_export(*args, **kwargs):
            nonlocal published
            validate(*args, **kwargs)
            if not published:
                published = True
                etl.export_census(source_zip=self.new, tables=['G02'], out=out)
        with patch.object(etl, '_validate_source_identity', side_effect=another_completed_export):
            etl.export_census(source_zip=self.new, out=out)
        manifest = json.loads((out / '2021_GCP_POA_NSW_manifest.json').read_text())
        self.assertEqual(set(manifest['tables']), {'G01', 'G02'})
        self.assertEqual(manifest['tables']['G02']['columns'],
                         ['POA_CODE_2021', 'Median_age_persons'])

    def test_orphan_warning_is_emitted_once_across_destination_preflights(self):
        out = self.root / 'orphan'
        (out / '.abs-export-orphan').mkdir(parents=True)
        with warnings.catch_warnings(record=True) as observed:
            warnings.simplefilter('always')
            etl.export_census(source_zip=self.new, out=out)
        messages = [str(w.message) for w in observed]
        self.assertEqual(sum('Staging folders without a recovery journal' in m for m in messages), 1)
        # The first export left a lock file; a folder without one has two preflights.
        out = self.root / 'orphan-later'
        (out / '.abs-export-orphan').mkdir(parents=True)
        validate = etl._validate_source_identity
        def introduce_orphan(*args, **kwargs):
            validate(*args, **kwargs)
            (out / '.abs-export-later').mkdir(exist_ok=True)
        with patch.object(etl, '_validate_source_identity', side_effect=introduce_orphan):
            with warnings.catch_warnings(record=True) as observed:
                warnings.simplefilter('always')
                etl.export_census(source_zip=self.new, out=out)
        messages = [str(w.message) for w in observed]
        self.assertEqual(len(messages), 2)
        self.assertIn(str(out / '.abs-export-orphan'), messages[0])
        self.assertIn(str(out / '.abs-export-later'), messages[1])
        self.assertNotIn(str(out / '.abs-export-orphan'), messages[1])

    def test_busy_explicit_export_checks_zip_first_only_without_a_lock_file(self):
        out = self.root / 'busy'
        events = []
        validate_zip = etl._validate_zip
        validate_identity = etl._validate_source_identity
        def structure(*args, **kwargs):
            events.append('structure')
            return validate_zip(*args, **kwargs)
        def identity(*args, **kwargs):
            events.append('identity')
            return validate_identity(*args, **kwargs)
        def busy(*args, **kwargs):
            events.append('lock')
            raise BlockingIOError('Output folder is busy')
        for lock_file, expected in [(False, ['structure', 'identity', 'lock']), (True, ['lock'])]:
            events.clear()
            if lock_file:
                (out / '.abs-census-etl.lock').touch()
            with self.subTest(lock_file=lock_file), ExitStack() as stack:
                stack.enter_context(patch.object(etl, '_validate_zip', side_effect=structure))
                stack.enter_context(patch.object(etl, '_validate_source_identity',
                                                 side_effect=identity))
                stack.enter_context(patch.object(etl, '_export_lock', side_effect=busy))
                for name in ['_resolve_source', '_read_tables_from_zip', 'load_csv']:
                    stack.enter_context(patch.object(etl, name,
                        side_effect=AssertionError('payload accessed')))
                with self.assertRaisesRegex(BlockingIOError, 'Output folder is busy'):
                    etl.export_census(source_zip=self.new, out=out)
                self.assertEqual(events, expected)

    def test_running_export_is_busy_before_its_staging_or_the_zip_is_inspected(self):
        out = self.root / 'running'
        etl.export_census(source_zip=self.new, out=out)
        # A real independent process holds the lock, as a running export would.
        script = ('import sys; from pathlib import Path; '
                  'from abs_census_etl import _export_lock; '
                  'ctx=_export_lock(Path(sys.argv[1])); ctx.__enter__(); '
                  'print("LOCKED",flush=True); sys.stdin.readline()')
        child = subprocess.Popen([sys.executable, '-c', script, str(out)], cwd=ROOT,
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True)
        try:
            self.assertEqual(child.stdout.readline().strip(), 'LOCKED')
            work = out / '.abs-export-running'
            work.mkdir()
            (work / '2021_GCP_POA_NSW_G01_wide.csv.part').write_text('POA_CODE_2021\n')
            for phase in ['staging', 'publishing']:
                if phase == 'publishing':
                    (work / 'recovery.json').write_text(json.dumps({'state': 'publishing'}))
                with self.subTest(phase=phase), ExitStack() as stack:
                    for name in ['ZipFile', '_validate_zip', '_validate_source_identity',
                                 '_resolve_source']:
                        stack.enter_context(patch.object(etl, name,
                            side_effect=AssertionError('source accessed while busy')))
                    with warnings.catch_warnings(record=True) as observed:
                        warnings.simplefilter('always')
                        with self.assertRaisesRegex(BlockingIOError, 'Output folder is busy'):
                            etl.export_census(source_zip=self.new, out=out)
                    self.assertEqual([str(w.message) for w in observed], [])
                    stderr = io.StringIO()
                    with patch('sys.stderr', stderr):
                        self.assertEqual(etl.cli(['--geo', 'POA', '--zip', str(self.new),
                                                  '--out', str(out)]), 1)
                    self.assertIn('Output folder is busy', stderr.getvalue())
                    self.assertNotIn('WARNING', stderr.getvalue())
                    self.assertNotIn('recovery journal', stderr.getvalue())
        finally:
            child.kill()
            child.communicate(timeout=15)
        self.assertTrue((work / 'recovery.json').exists())

if __name__ == '__main__':
    unittest.main()
