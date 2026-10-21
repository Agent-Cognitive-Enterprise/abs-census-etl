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



# Observed G01 samples from the accepted 2016 all-state structural survey.
# Full pack values are tested separately; small fixtures reuse these real IDs.
GEO_SAMPLES = {('CED', 'ACT'): ['CED801', 'CED802'],
 ('CED', 'AUS'): ['CED101', 'CED102'],
 ('CED', 'NSW'): ['CED101', 'CED102'],
 ('CED', 'NT'): ['CED701', 'CED702'],
 ('CED', 'OT'): ['CED994', 'CED997'],
 ('CED', 'QLD'): ['CED301', 'CED302'],
 ('CED', 'SA'): ['CED401', 'CED402'],
 ('CED', 'TAS'): ['CED601', 'CED602'],
 ('CED', 'VIC'): ['CED201', 'CED202'],
 ('CED', 'WA'): ['CED501', 'CED502'],
 ('LGA', 'ACT'): ['LGA89399', 'LGA89499'],
 ('LGA', 'AUS'): ['LGA10050', 'LGA10130'],
 ('LGA', 'NSW'): ['LGA10050', 'LGA10130'],
 ('LGA', 'NT'): ['LGA70200', 'LGA70420'],
 ('LGA', 'OT'): ['LGA99399', 'LGA99499'],
 ('LGA', 'QLD'): ['LGA30250', 'LGA30300'],
 ('LGA', 'SA'): ['LGA40070', 'LGA40120'],
 ('LGA', 'TAS'): ['LGA60210', 'LGA60410'],
 ('LGA', 'VIC'): ['LGA20110', 'LGA20260'],
 ('LGA', 'WA'): ['LGA50080', 'LGA50210'],
 ('POA', 'ACT'): ['POA2600', 'POA2601'],
 ('POA', 'AUS'): ['POA2000', 'POA2006'],
 ('POA', 'NSW'): ['POA2000', 'POA2006'],
 ('POA', 'NT'): ['POA0800', 'POA0810'],
 ('POA', 'OT'): ['POA2899', 'POA6798'],
 ('POA', 'QLD'): ['POA4000', 'POA4005'],
 ('POA', 'SA'): ['POA5000', 'POA5005'],
 ('POA', 'TAS'): ['POA7000', 'POA7001'],
 ('POA', 'VIC'): ['POA3000', 'POA3002'],
 ('POA', 'WA'): ['POA6000', 'POA6003'],
 ('SA1', 'ACT'): ['8100101', '8100102'],
 ('SA1', 'AUS'): ['1100701', '1100702'],
 ('SA1', 'NSW'): ['1100701', '1100702'],
 ('SA1', 'NT'): ['7100101', '7100204'],
 ('SA1', 'OT'): ['9100101', '9100102'],
 ('SA1', 'QLD'): ['3100101', '3100102'],
 ('SA1', 'SA'): ['4100101', '4100102'],
 ('SA1', 'TAS'): ['6100101', '6100102'],
 ('SA1', 'VIC'): ['2100101', '2100102'],
 ('SA1', 'WA'): ['5100101', '5100102'],
 ('SA2', 'ACT'): ['801011001', '801011002'],
 ('SA2', 'AUS'): ['101021007', '101021008'],
 ('SA2', 'NSW'): ['101021007', '101021008'],
 ('SA2', 'NT'): ['701011001', '701011002'],
 ('SA2', 'OT'): ['901011001', '901021002'],
 ('SA2', 'QLD'): ['301011001', '301011002'],
 ('SA2', 'SA'): ['401011001', '401011002'],
 ('SA2', 'TAS'): ['601011001', '601011002'],
 ('SA2', 'VIC'): ['201011001', '201011002'],
 ('SA2', 'WA'): ['501011001', '501011002'],
 ('SA3', 'ACT'): ['80101', '80103'],
 ('SA3', 'AUS'): ['10102', '10103'],
 ('SA3', 'NSW'): ['10102', '10103'],
 ('SA3', 'NT'): ['70101', '70102'],
 ('SA3', 'OT'): ['90101', '90102'],
 ('SA3', 'QLD'): ['30101', '30102'],
 ('SA3', 'SA'): ['40101', '40102'],
 ('SA3', 'TAS'): ['60101', '60102'],
 ('SA3', 'VIC'): ['20101', '20102'],
 ('SA3', 'WA'): ['50101', '50102'],
 ('SA4', 'ACT'): ['801', '897'],
 ('SA4', 'AUS'): ['101', '102'],
 ('SA4', 'NSW'): ['101', '102'],
 ('SA4', 'NT'): ['701', '702'],
 ('SA4', 'OT'): ['901', '997'],
 ('SA4', 'QLD'): ['301', '302'],
 ('SA4', 'SA'): ['401', '402'],
 ('SA4', 'TAS'): ['601', '602'],
 ('SA4', 'VIC'): ['201', '202'],
 ('SA4', 'WA'): ['501', '502'],
 ('SED', 'ACT'): ['SED80001', 'SED80002'],
 ('SED', 'AUS'): ['SED10001', 'SED10002'],
 ('SED', 'NSW'): ['SED10001', 'SED10002'],
 ('SED', 'NT'): ['SED70001', 'SED70002'],
 ('SED', 'OT'): ['SED99191', 'SED99494'],
 ('SED', 'QLD'): ['SED30001', 'SED30002'],
 ('SED', 'SA'): ['SED40001', 'SED40002'],
 ('SED', 'TAS'): ['SED60101', 'SED60106'],
 ('SED', 'VIC'): ['SED20106', 'SED20207'],
 ('SED', 'WA'): ['SED50106', 'SED50205'],
 ('SSC', 'ACT'): ['SSC80001', 'SSC80002'],
 ('SSC', 'AUS'): ['SSC10001', 'SSC10002'],
 ('SSC', 'NSW'): ['SSC10001', 'SSC10002'],
 ('SSC', 'NT'): ['SSC70001', 'SSC70002'],
 ('SSC', 'OT'): ['SSC90001', 'SSC90002'],
 ('SSC', 'QLD'): ['SSC30001', 'SSC30002'],
 ('SSC', 'SA'): ['SSC40001', 'SSC40002'],
 ('SSC', 'TAS'): ['SSC60001', 'SSC60002'],
 ('SSC', 'VIC'): ['SSC20001', 'SSC20002'],
 ('SSC', 'WA'): ['SSC50001', 'SSC50002'],
 ('STE', 'ACT'): ['8'],
 ('STE', 'AUS'): ['1', '2'],
 ('STE', 'NSW'): ['1'],
 ('STE', 'NT'): ['7'],
 ('STE', 'OT'): ['9'],
 ('STE', 'QLD'): ['3'],
 ('STE', 'SA'): ['4'],
 ('STE', 'TAS'): ['6'],
 ('STE', 'VIC'): ['2'],
 ('STE', 'WA'): ['5']}
CODE_COLUMNS_2016 = {
    'POA': 'POA_CODE_2016', 'SA1': 'SA1_7DIGITCODE_2016', 'SA2': 'SA2_MAINCODE_2016',
    **{g: f'{g}_CODE_2016' for g in ['SA3', 'SA4', 'LGA', 'SED', 'CED', 'SSC', 'STE']},
}


def geography_pack(path, geo, state='NSW', *, revision=None):
    year_pack(path, state=state)
    with ZipFile(path) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    with ZipFile(path, 'w', compression=ZIP_DEFLATED) as archive:
        for name, body in entries.items():
            if name.endswith('.csv'):
                name = name.replace('_POA.csv', f'_{geo}.csv').replace(
                    'Postal Areas', etl.GEOGRAPHIES.get(geo, 'State Suburbs'))
                lines = body.decode().splitlines()
                measures = lines[0].split(',')[1:]
                values = lines[1].split(',')[1:]
                body = (CODE_COLUMNS_2016[geo] + ',' + ','.join(measures) + '\n'
                        + ''.join(code + ',' + ','.join(values) + '\n'
                                  for code in GEO_SAMPLES[(geo, state)])).encode()
            archive.writestr(name, body)
        if revision is not None:
            archive.writestr('Readme/Summary_of_Changes.txt', revision)
    return path


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
        out = self.root / 'integral-year-export'
        etl.export_census(year=np.int64(2016), source_zip=self.old, out=out)
        manifest = json.loads((out/'2016_GCP_POA_NSW_manifest.json').read_text())
        self.assertEqual(manifest['census_year'], 2016)
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
                for geo in ['SAL', 'UNKNOWN']:
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

    def test_unsupported_export_year_is_refused_before_source_or_output(self):
        out = self.root / 'not-created'
        with patch.object(etl, '_resolve_source', side_effect=AssertionError('source accessed')):
            with self.assertRaisesRegex(ValueError, 'Unsupported Census year'):
                etl.export_census(year=2011, out=out)
        self.assertFalse(out.exists())

    def test_cli_discovers_and_exports_2016(self):
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
        stdout = io.StringIO()
        with patch('sys.stderr', stderr), patch('sys.stdout', stdout):
            status = etl.cli(['--geo', 'POA', '--year', '2016', '--zip', str(self.old),
                              '--out', str(out)])
        self.assertEqual(status, 0, stderr.getvalue())
        self.assertTrue((out / '2016_GCP_POA_NSW_manifest.json').is_file())
        self.assertIn(f'Manifest: {out / "2016_GCP_POA_NSW_manifest.json"}', stdout.getvalue(),
                      'CLI manifest path lost selected year')

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

    def test_supported_other_year_geography_suggests_explicit_year(self):
        source = self.root / '2016-sa1.zip'
        with ZipFile(source, 'w') as archive:
            archive.writestr('2016Census_G01_NSW_SA1.csv',
                             'SA1_7DIGITCODE_2016,Tot_P_P\n1100701,1\n')
        with self.assertRaises(ValueError) as caught:
            etl.load_census(geo='SA1', source_zip=source)
        self.assertIn('found 2016', str(caught.exception))
        self.assertIn('--year 2016', str(caught.exception))

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
            etl._VINTAGES[2016]['columns']['UNKNOWN'] = 'UNREVIEWED_CODE'
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

    def test_all_2016_geographies_states_headers_and_descriptions(self):
        for geo, state in GEO_SAMPLES:
            with self.subTest(geo=geo, state=state):
                source = geography_pack(self.root / 'geo.zip', geo, state)
                code = GEO_SAMPLES[(geo, state)][0]
                frame = etl.load_census(year=2016, geo=geo.lower(), state=state.lower(),
                                        source_zip=source, codes=[f' {code.lower()} '])
                self.assertEqual(frame.columns[0], CODE_COLUMNS_2016[geo])
                self.assertEqual(frame.iloc[:, 0].tolist(), [code])
                long = etl.load_census(year=2016, geo=geo, state=state, source_zip=source,
                                       table='G04', codes=[code], long=True)
                self.assertEqual(long.columns.tolist(), [CODE_COLUMNS_2016[geo], 'variable', 'value'])
                self.assertEqual(len(etl.describe_census(year=2016, geo=geo, state=state,
                                                        source_zip=source)), 3)
                with ZipFile(source) as archive:
                    self.assertEqual(etl.find_members(archive, 'G01', geo, year=2016),
                                     etl.find_members(archive, 'G01', geo, state, year=2016))
        self.assertEqual(set(CODE_COLUMNS_2016), set(etl._VINTAGES[2016]['columns']))

    def test_2016_identifiers_are_not_translated_and_partial_misses_fail(self):
        for geo in ['SA1', 'SA2', 'SSC', 'LGA']:
            source = geography_pack(self.root / 'geo.zip', geo)
            code = GEO_SAMPLES[(geo, 'NSW')][0]
            with self.assertRaisesRegex(ValueError, 'Unmatched'):
                etl.load_census(year=2016, geo=geo, source_zip=source, codes=[code, '99999999999'])
            if geo == 'SA1':
                with self.assertRaisesRegex(ValueError, 'Unmatched SA1'):
                    etl.load_census(year=2016, geo=geo, source_zip=source, codes=['10102100701'])
        for year, geo in [(2016, 'SAL'), (2021, 'SSC')]:
            stderr = io.StringIO()
            with patch('sys.stderr', stderr), patch.object(etl, '_resolve_source',
                    side_effect=AssertionError('source accessed')):
                with self.assertRaises(SystemExit) as error:
                    etl.cli(['--year', str(year), '--geo', geo, '--list-tables'])
                self.assertEqual(error.exception.code, 2)
            self.assertIn('Unsupported geography', stderr.getvalue())
            self.assertIn('choose ' + ', '.join(sorted(etl._vintage(year)['columns'])),
                          stderr.getvalue())

    def test_year_qualified_exports_coexist_and_preserve_source_headers(self):
        out = self.root / 'both-years'
        for geo in CODE_COLUMNS_2016:
            source = geography_pack(self.root / 'geo-export.zip', geo)
            code = GEO_SAMPLES[(geo, 'NSW')][0]
            for long in [False, True]:
                files = etl.export_census(year=2016, geo=geo, source_zip=source, out=out,
                                          codes=[code], tables=['G01', 'G04'], long=long)
                manifest_path = out / f'2016_GCP_{geo}_NSW_manifest.json'
                self.assertTrue(manifest_path.is_file(), 'manifest path lost Census year')
                manifest = json.loads(manifest_path.read_text())
                self.assertEqual(manifest['census_year'], 2016)
                self.assertEqual(manifest['asgs_edition'], 2)
                self.assertEqual(manifest['manifest_schema_version'], 1)
                self.assertEqual(manifest['data_years'], [2016])
                self.assertEqual(manifest['boundary']['source_code_column'], CODE_COLUMNS_2016[geo])
                self.assertIsNone(manifest['boundary']['reference_date'])
                for table, file in files.items():
                    data = pd.read_csv(file, dtype={CODE_COLUMNS_2016[geo]: 'string'})
                    self.assertEqual(data.columns[0], CODE_COLUMNS_2016[geo])
                    self.assertEqual(set(data.iloc[:, 0]), {code})
                    self.assertEqual(manifest['tables'][table]['census_year'], 2016)
                    self.assertEqual(manifest['tables'][table]['data_years'], [2016])
        etl.export_census(source_zip=self.new, out=out)
        self.assertTrue((out/'2021_GCP_POA_NSW_manifest.json').is_file())
        self.assertTrue((out/'2016_GCP_POA_NSW_manifest.json').is_file())

    def test_format_conflicts_are_year_specific_and_bad_identity_still_has_no_artifacts(self):
        out = self.root / 'formats'
        out.mkdir()
        (out/'2021_GCP_POA_NSW_G01_wide.parquet').write_bytes(b'existing')
        etl.export_census(year=2016, source_zip=self.old, out=out)
        with patch.object(etl, '_resolve_source', side_effect=AssertionError('source accessed')):
            with self.assertRaises(FileExistsError):
                etl.export_census(source_zip=self.new, out=out)
        for source in [self.new]:
            for exists in [False, True]:
                bad_out = self.root / ('existing' if exists else 'fresh')
                if exists:
                    bad_out.mkdir()
                with self.assertRaisesRegex(ValueError, 'Wrong Census year'):
                    etl.export_census(year=2016, source_zip=source, out=bad_out)
                self.assertTrue(not bad_out.exists() or not list(bad_out.iterdir()))

    def test_both_export_years_use_one_existing_output_lock(self):
        out = self.root / 'shared'
        out.mkdir()
        with etl._export_lock(out):
            for year, source in [(2016, self.old), (2021, self.new)]:
                with patch.object(etl, '_resolve_source', side_effect=AssertionError('source accessed')):
                    with self.assertRaisesRegex(BlockingIOError, 'busy'):
                        etl.export_census(year=year, source_zip=source, out=out)

    def test_publish_failure_rolls_back_data_and_manifest_in_both_years(self):
        replace = Path.replace
        for year, source in [(2016, self.old), (2021, self.new)]:
            out = self.root / f'rollback-{year}'
            etl.export_census(year=year, source_zip=source, tables=['G01', 'G04'], out=out)
            before = {p.name: p.read_bytes() for p in out.iterdir()}
            blocked = f'{year}_GCP_POA_NSW_G04_wide.csv.part'
            def fail_second(path, target):
                if path.name == blocked:
                    raise OSError('injected second publish failure')
                return replace(path, target)
            with patch.object(Path, 'replace', fail_second):
                with self.assertRaisesRegex(OSError, 'second publish failure'):
                    etl.export_census(year=year, source_zip=source, codes=['2000'],
                                      tables=['G01', 'G04'], out=out)
            self.assertEqual(before, {p.name: p.read_bytes() for p in out.iterdir()})

    def test_optional_table_history_never_inherits_latest_top_level(self):
        captured = json.loads((ROOT/'tests/fixtures/v020_manifest.json').read_text())
        manifest = captured['manifest']
        self.assertEqual(captured['old_tag'], 'v0.2.0')
        out = self.root / 'legacy'
        out.mkdir()
        for field, value in [('source_revision', {'status': 'unknown', 'documents': []}),
                             ('boundary', {'edition': 'latest'}), ('data_years', [9999])]:
            manifest[field] = value
        # Earlier development manifests also wrote explicit null identity fields.
        manifest['tables']['G02'].update(
            {field: None for field in ['census_year', 'profile', 'data_years', 'boundary']})
        (out/'2021_GCP_POA_NSW_manifest.json').write_text(json.dumps(manifest))
        etl.export_census(source_zip=self.new, tables=['G01'], out=out)
        final = json.loads((out/'2021_GCP_POA_NSW_manifest.json').read_text())
        legacy = final['tables']['G02']
        self.assertIsNone(legacy['source_revision'])
        self.assertEqual(legacy['census_year'], 2021, 'legacy identity was not derived')
        self.assertEqual(legacy['profile'], 'GCP')
        self.assertEqual(legacy['data_years'], [2021])
        self.assertEqual(legacy['boundary'], etl._boundary_details('POA', 2021))
        self.assertEqual(legacy['source_sha256'], manifest['tables']['G02']['source_sha256'])
        self.assertNotIn('writer_version', legacy)
        self.assertEqual(final['tables']['G01']['data_years'], [2021])

    def test_unsupported_manifest_schema_fails_before_source(self):
        for schema in [0, 2, True, 1.0, '1', None]:
            out = self.root / f'schema-{schema!r}'
            etl.export_census(source_zip=self.new, out=out)
            path = out/'2021_GCP_POA_NSW_manifest.json'
            manifest = json.loads(path.read_text())
            manifest['manifest_schema_version'] = schema
            manifest['asgs_edition'] = 999
            path.write_text(json.dumps(manifest))
            with patch.object(etl, 'ZipFile', side_effect=AssertionError('source accessed')):
                with self.assertRaisesRegex(ValueError, 'Unsupported manifest schema'):
                    etl.export_census(source_zip=self.new, out=out)

    def test_revision_documents_preserve_bytes_encoding_and_table_history(self):
        old_revision = b'# First revision\nG14 updated\n'
        latest_revision = '# Second revision \u2013 G36 renamed\n'.encode('cp1252')
        out = self.root / 'revisions'
        for body, table in [(old_revision, 'G01'), (latest_revision, 'G04')]:
            source = geography_pack(self.root / f'{table}.zip', 'POA', revision=body)
            etl.export_census(year=2016, source_zip=source, tables=[table], out=out)
        result = json.loads((out/'2016_GCP_POA_NSW_manifest.json').read_text())
        self.assertEqual(result['tables']['G01']['source_revision']['status'], 'documented')
        self.assertEqual(result['tables']['G01']['source_revision']['documents'][0]['encoding'], 'ascii')
        latest = result['tables']['G04']['source_revision']['documents'][0]
        self.assertEqual(latest['encoding'], 'cp1252')
        self.assertTrue(latest['encoding_inferred'])
        self.assertNotIn('text', latest)
        self.assertIn('\u2013', result['source_documents'][latest['sha256']]['text'])
        import hashlib
        self.assertEqual(latest['sha256'], hashlib.sha256(latest_revision).hexdigest())
        source = geography_pack(self.root/'bom.zip', 'POA', revision=b'\xef\xbb\xbf# BOM release\n')
        with ZipFile(source) as archive:
            document = etl._source_revision(archive)['documents'][0]
        self.assertEqual(document['encoding'], 'utf-8-sig')
        self.assertEqual(document['text'], '# BOM release\n')

    def test_year_specific_download_url_is_recorded_by_exports(self):
        class Response:
            def __init__(self, body): self.body = body
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def raise_for_status(self): pass
            def iter_content(self, **kwargs): yield self.body
        for year, source in [(2016, self.old), (2021, self.new)]:
            out = self.root / f'download-{year}'
            with patch.object(etl.requests, 'get', return_value=Response(source.read_bytes())) as get:
                etl.export_census(year=year, cache_dir=self.root / f'cache-{year}', out=out)
            url = etl.datapack_url('POA', 'NSW', year=year)
            self.assertEqual(get.call_args.args[0], url)
            manifest = json.loads((out / f'{year}_GCP_POA_NSW_manifest.json').read_text())
            self.assertEqual(manifest['source_url'], url, 'source URL lost selected year')


    def test_boundary_editions_and_administrative_types_are_explicit(self):
        for year, edition in [(2016, 2), (2021, 3)]:
            for geo in etl._vintage(year)['columns']:
                boundary = etl._boundary_details(geo, year)
                self.assertEqual(boundary['edition'], edition, 'wrong boundary edition')
                self.assertEqual(boundary['geography'], geo)
                structure = ('main_structure' if geo in {'SA1', 'SA2', 'SA3', 'SA4', 'STE'}
                             else 'non_abs_structure')
                self.assertEqual(boundary['structure'], structure, 'wrong ABS structure')
                if geo in {'LGA', 'SED', 'CED', 'STE'}:
                    self.assertEqual(boundary['boundary_type'], 'administrative',
                                     'wrong administrative boundary type')
                elif geo == 'POA':
                    self.assertEqual(boundary['boundary_type'], 'postal_approximation',
                                     'wrong postal boundary type')
                elif geo in {'SSC', 'SAL'}:
                    self.assertEqual(boundary['boundary_type'], 'locality',
                                     'wrong locality boundary type')
                else:
                    self.assertEqual(boundary['boundary_type'], 'statistical')

    def test_unmarked_utf8_revision_encoding_is_inferred(self):
        source = geography_pack(self.root/'inferred.zip', 'POA', revision=b'\xc3\xa9')
        with ZipFile(source) as archive:
            doc = etl._source_revision(archive)['documents'][0]
        self.assertEqual(doc['encoding'], 'utf-8')
        self.assertTrue(doc['encoding_inferred'], 'unmarked encoding must be inferred')

    def test_revision_size_and_decoding_failures_do_not_block_exports(self):
        import hashlib
        for body, status in [(b'x' * (2 * 1024 * 1024 + 1), 'oversize'),
                             (b'bad byte \x81', 'undecodable')]:
            source = geography_pack(self.root/(status+'.zip'), 'POA', revision=body)
            out = self.root/status
            etl.export_census(year=2016, source_zip=source, out=out)
            result = json.loads((out/'2016_GCP_POA_NSW_manifest.json').read_text())
            doc = result['source_revision']['documents'][0]
            self.assertEqual(doc.get('status'), status, 'advisory document status lost')
            self.assertEqual(result['source_revision']['status'], 'unavailable',
                             'unavailable revision status lost')
            self.assertIsNone(doc['encoding'])
            self.assertIsNone(doc['encoding_inferred'])
            self.assertEqual(doc['text_status'], status)
            self.assertEqual(doc['sha256'], hashlib.sha256(body).hexdigest())
            self.assertNotIn('text', doc)
            self.assertIsNone(result['source_documents'][doc['sha256']]['text'])
            self.assertTrue((out/'2016_GCP_POA_NSW_G01_wide.csv').is_file())
            with ZipFile(source) as archive:
                self.assertEqual(etl._source_revision(archive)['status'], 'unavailable',
                                 'raw unavailable revision status lost')

    def test_revision_text_is_stored_once_and_pruned_when_unreferenced(self):
        body = b'# Documented revision\n' + b'x'*200000
        source = geography_pack(self.root/'large-document.zip', 'POA', revision=body)
        out = self.root/'deduplication'
        etl.export_census(year=2016, source_zip=source, tables=['G01', 'G04'], out=out)
        path = out/'2016_GCP_POA_NSW_manifest.json'
        self.assertLess(path.stat().st_size, 230000, 'revision text was duplicated')
        manifest = json.loads(path.read_text())
        self.assertEqual(len(manifest['source_documents']), 1)
        self.assertNotIn('text', manifest['tables']['G01']['source_revision']['documents'][0])
        source = geography_pack(self.root/'no-document.zip', 'POA')
        etl.export_census(year=2016, source_zip=source, tables=['G01'], out=out)
        self.assertEqual(len(json.loads(path.read_text())['source_documents']), 1)
        etl.export_census(year=2016, source_zip=source, tables=['G04'], out=out)
        self.assertEqual(json.loads(path.read_text())['source_documents'], {})

    def test_current_writer_version_is_recorded_without_inventing_legacy_writer(self):
        import tomllib
        with (ROOT/'pyproject.toml').open('rb') as source:
            expected = tomllib.load(source)['project']['version']
        self.assertEqual(etl._writer_version(), expected)
        out = self.root/'writer-version'
        etl.export_census(year=2016, source_zip=self.old, out=out)
        manifest = json.loads((out/'2016_GCP_POA_NSW_manifest.json').read_text())
        self.assertEqual(manifest['writer_version'], expected)
        self.assertEqual(manifest['tables']['G01']['writer_version'], expected)

    def test_forged_stored_document_is_refused_before_source(self):
        source = geography_pack(self.root/'document.zip', 'POA', revision=b'# Real wording\n')
        out = self.root/'forged-store'
        etl.export_census(year=2016, source_zip=source, out=out)
        path = out/'2016_GCP_POA_NSW_manifest.json'
        manifest = json.loads(path.read_text())
        document = next(iter(manifest['source_documents'].values()))
        document['text'] = 'FORGED TEXT'
        path.write_text(json.dumps(manifest))
        with patch.object(etl, 'ZipFile', side_effect=AssertionError('source accessed')):
            with self.assertRaisesRegex(ValueError, 'Revision document SHA-256 mismatch'):
                etl.export_census(year=2016, source_zip=source, out=out)
        self.assertEqual(json.loads(path.read_text()), manifest)

    def test_stored_document_hash_roundtrips_each_supported_encoding(self):
        for encoding, body in [('ascii', b'# Revision\n'),
                               ('utf-8', '# Revision é\n'.encode('utf-8')),
                               ('utf-8-sig', '# Revision é\n'.encode('utf-8-sig')),
                               ('cp1252', '# Revision –\n'.encode('cp1252'))]:
            source = geography_pack(self.root/(encoding+'.zip'), 'POA', revision=body)
            out = self.root/encoding
            etl.export_census(year=2016, source_zip=source, out=out)
            etl.export_census(year=2016, source_zip=source, tables=['G04'], out=out)
            manifest = json.loads((out/'2016_GCP_POA_NSW_manifest.json').read_text())
            doc = manifest['tables']['G01']['source_revision']['documents'][0]
            self.assertEqual(doc['encoding'], encoding)
            self.assertEqual(doc['text_status'], 'available')
            self.assertEqual(len(manifest['source_documents']), 1)

    def test_contradictory_retained_table_identity_is_refused_before_source(self):
        out = self.root/'contradictory'
        etl.export_census(source_zip=self.new, tables=['G01', 'G02'], out=out)
        path = out/'2021_GCP_POA_NSW_manifest.json'
        original = json.loads(path.read_text())
        for field, value in [('census_year', 2016), ('profile', 'TSP'),
                             ('data_years', [2011]), ('boundary', {'structure': 'statistical'})]:
            manifest = json.loads(json.dumps(original))
            manifest['tables']['G02'][field] = value
            path.write_text(json.dumps(manifest))
            with patch.object(etl, 'ZipFile', side_effect=AssertionError('source accessed')):
                with self.assertRaisesRegex(ValueError, 'Incompatible existing manifest.*contradicts'):
                    etl.export_census(source_zip=self.new, out=out)
            self.assertEqual(json.loads(path.read_text()), manifest)

    def test_malformed_revision_and_store_are_clean_preflight_errors(self):
        source = geography_pack(self.root/'shape.zip', 'POA', revision=b'# Revision\n')
        out = self.root/'shapes'
        etl.export_census(year=2016, source_zip=source, out=out)
        path = out/'2016_GCP_POA_NSW_manifest.json'
        original = json.loads(path.read_text())
        variants = []
        for revision in [5, [], {}, {'status': 'documented', 'documents': 5},
                         {'status': [], 'documents': []},
                         {'status': 'documented', 'documents': [5]},
                         {'status': 'documented', 'documents': [{'status': []}]},
                         {'status': 'documented', 'documents': [{'sha256': 'bad'}]}]:
            for location in ['table', 'summary']:
                manifest = json.loads(json.dumps(original))
                target = manifest['tables']['G01'] if location == 'table' else manifest
                target['source_revision'] = revision
                variants.append(manifest)
        for key, value in [('encoding', ['bad']), ('encoding_inferred', []),
                           ('sha256', []), ('member', []), ('text_status', [])]:
            manifest = json.loads(json.dumps(original))
            manifest['tables']['G01']['source_revision']['documents'][0][key] = value
            variants.append(manifest)
        for store in [None, [], {'bad': {}}, {'a'*64: []},
                      {'a'*64: {'status': [], 'text': 'wording'}}]:
            manifest = json.loads(json.dumps(original))
            manifest['source_documents'] = store
            variants.append(manifest)
        for manifest in variants:
            path.write_text(json.dumps(manifest))
            stderr = io.StringIO()
            with patch('sys.stderr', stderr), patch.object(etl, 'ZipFile',
                    side_effect=AssertionError('source accessed')):
                self.assertEqual(etl.cli(['--geo', 'POA', '--year', '2016', '--zip', str(source),
                                           '--out', str(out)]), 1)
            self.assertIn('ERROR: Invalid existing manifest:', stderr.getvalue())
            self.assertIn('fresh output folder', stderr.getvalue())
            self.assertNotIn('Traceback', stderr.getvalue())

    def test_unreleased_inline_documents_are_refused_not_migrated(self):
        source = geography_pack(self.root/'inline.zip', 'POA', revision=b'# Revision\n')
        out = self.root/'inline-format'
        etl.export_census(year=2016, source_zip=source, out=out)
        path = out/'2016_GCP_POA_NSW_manifest.json'
        manifest = json.loads(path.read_text())
        reference = manifest['tables']['G01']['source_revision']['documents'][0]
        reference['text'] = '# Revision\n'
        path.write_text(json.dumps(manifest))
        with patch.object(etl, 'ZipFile', side_effect=AssertionError('source accessed')):
            with self.assertRaisesRegex(ValueError, 'regenerate unreleased inline documents'):
                etl.export_census(year=2016, source_zip=source, out=out)

    def assert_provenance_refused(self, name, revision, change, message):
        """Each case is otherwise valid, so only the guard under test can refuse it."""
        source = geography_pack(self.root/(name+'.zip'), 'POA', revision=revision)
        out = self.root/name
        etl.export_census(year=2016, source_zip=source, out=out)
        path = out/'2016_GCP_POA_NSW_manifest.json'
        manifest = json.loads(path.read_text())
        # The summary and the G01 table both refer to the single stored document.
        change(next(iter(manifest['source_documents'].values())),
               [entry['source_revision']['documents'][0]
                for entry in [manifest, manifest['tables']['G01']]])
        path.write_text(json.dumps(manifest))
        with patch.object(etl, 'ZipFile', side_effect=AssertionError(
                'source accessed despite ' + name)):
            with self.assertRaisesRegex(ValueError, message):
                etl.export_census(year=2016, source_zip=source, out=out)
        self.assertEqual(json.loads(path.read_text()), manifest)

    def test_unavailable_stored_document_with_text_is_refused(self):
        # Oversize/undecodable bodies are never rehashed, so text must stay null.
        def forge(document, references):
            document['text'] = 'FORGED TEXT'
        self.assert_provenance_refused('unavailable-text', b'bad byte \x81', forge,
                                       'Unavailable revision document must have null text')

    def test_valid_reference_disagreeing_with_store_is_refused(self):
        def disagree(document, references):
            references[-1].update(encoding='utf-8', encoding_inferred=True)
        self.assert_provenance_refused('reference-disagreement', b'# Revision\n', disagree,
                                       'Revision reference disagrees with stored document')

    def test_inconsistent_encoding_inference_is_refused(self):
        for name, body in [('ascii-inference', b'# Revision\n'),
                           ('cp1252-inference', '# Revision –\n'.encode('cp1252'))]:
            def flip(document, references):
                for record in [document, *references]:
                    record['encoding_inferred'] = not record['encoding_inferred']
            with self.subTest(name=name):
                self.assert_provenance_refused(name, body, flip,
                                               'Invalid revision encoding inference')

    def test_empty_revision_member_is_refused(self):
        def empty(document, references):
            references[-1]['member'] = ''
        self.assert_provenance_refused('empty-member', b'# Revision\n', empty,
                                       'Invalid revision reference')

    def test_missing_retained_document_text_is_explicit_and_restorable(self):
        source = geography_pack(self.root/'retained.zip', 'POA', revision=b'# Retained\n')
        out = self.root/'missing-text'
        etl.export_census(year=2016, source_zip=source, out=out)
        path = out/'2016_GCP_POA_NSW_manifest.json'
        manifest = json.loads(path.read_text())
        manifest.pop('source_documents')  # Same loss as a 0.2.0 write.
        path.write_text(json.dumps(manifest))
        other = geography_pack(self.root/'other.zip', 'POA')
        etl.export_census(year=2016, source_zip=other, tables=['G04'], out=out)
        manifest = json.loads(path.read_text())
        history = manifest['tables']['G01']['source_revision']
        self.assertEqual(history['status'], 'unavailable', 'lost document text not reflected')
        self.assertEqual(history['documents'][0]['text_status'], 'missing')
        etl.export_census(year=2016, source_zip=source, tables=['G04'], out=out)
        # Availability is finalized against all documents in the new inventory.
        manifest = json.loads(path.read_text())
        self.assertIn(history['documents'][0]['sha256'], manifest['source_documents'])
        restored = manifest['tables']['G01']['source_revision']
        self.assertEqual(restored['status'], 'documented')
        self.assertEqual(restored['documents'][0]['text_status'], 'available')

    def test_writer_version_metadata_must_own_the_running_module(self):
        copied = self.root/'copied.py'
        copied.write_text('# copied module identity fixture')
        class Metadata:
            version = '0.1.0.dev0'
            files = ['abs_census_etl.py']
            def locate_file(self, file): return ROOT/file
        with patch.object(etl, '__file__', str(copied)):
            with patch.object(etl, 'distribution', return_value=Metadata()):
                self.assertEqual(etl._writer_version(), 'unknown', 'unowned metadata accepted')
            with patch.object(etl, 'distribution', side_effect=etl.PackageNotFoundError('abs-census-etl')):
                self.assertEqual(etl._writer_version(), 'unknown')
            class Owner(Metadata):
                version = '9.9.9'
                def locate_file(self, file): return copied
            with patch.object(etl, 'distribution', return_value=Owner()):
                self.assertEqual(etl._writer_version(), '9.9.9')

    def test_writer_version_ignores_unrelated_neighbouring_pyproject(self):
        folder = self.root/'vendored'
        folder.mkdir()
        module = folder/'abs_census_etl.py'
        module.write_text('# vendored module identity fixture')
        project = folder/'pyproject.toml'
        with patch.object(etl, '__file__', str(module)), patch.object(
                etl, 'distribution', side_effect=etl.PackageNotFoundError('abs-census-etl')):
            project.write_text('[project]\nname = "host-project"\nversion = "9.9.9"\n')
            self.assertEqual(etl._writer_version(), 'unknown', 'unrelated project version accepted')
            project.write_text('[project]\nname = "abs-census-etl"\nversion = "1.2.3"\n')
            self.assertEqual(etl._writer_version(), '1.2.3')
            project.write_text('[project\n')
            self.assertEqual(etl._writer_version(), 'unknown')

    def test_copied_module_exports_with_unknown_writer_version(self):
        copied = self.root/'abs_census_etl.py'
        shutil.copy2(ROOT/'abs_census_etl.py', copied)
        out = self.root/'copied-output'
        result = subprocess.run([sys.executable, str(copied), '--geo', 'POA',
                                 '--zip', str(self.new), '--out', str(out)],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        manifest = json.loads((out/'2021_GCP_POA_NSW_manifest.json').read_text())
        self.assertEqual(manifest['writer_version'], 'unknown')
        self.assertEqual(manifest['tables']['G01']['writer_version'], 'unknown')
        self.assertNotIn('Traceback', result.stderr)

    def test_wrong_asgs_edition_is_refused_before_source(self):
        out = self.root / 'wrong-edition'
        etl.export_census(year=2016, source_zip=self.old, out=out)
        path = out/'2016_GCP_POA_NSW_manifest.json'
        manifest = json.loads(path.read_text())
        manifest['asgs_edition'] = 3
        path.write_text(json.dumps(manifest))
        with patch.object(etl, 'ZipFile', side_effect=AssertionError('source accessed')):
            with self.assertRaisesRegex(ValueError, 'Incompatible existing manifest'):
                etl.export_census(year=2016, source_zip=self.old, out=out)

    def test_old_writer_source_requires_exact_checkout_and_published_hash(self):
        import runpy
        checks = runpy.run_path(str(ROOT/'tests/check_compat.py'))
        child = self.root/'archive'
        child.mkdir()
        class Result:
            returncode = 0
            stdout = str(self.root)
        with patch.object(subprocess, 'run', return_value=Result()):
            with self.assertRaisesRegex(RuntimeError, 'parent repository'):
                checks['read_old_source'](child)
        class Source:
            returncode = 0
            stdout = b'forged old writer'
        Result.stdout = str(child)
        with patch.object(subprocess, 'run', side_effect=[Result(), Source()]):
            with self.assertRaisesRegex(RuntimeError, 'source SHA-256 differs'):
                checks['read_old_source'](child)

    def test_actual_old_writer_check_reports_missing_tag_clearly(self):
        import runpy
        checks = runpy.run_path(str(ROOT/'tests/check_compat.py'))
        with self.assertRaisesRegex(RuntimeError, 'needs a Git checkout containing'):
            checks['read_old_source'](self.root)

    @unittest.skipUnless(etl.find_spec('pyarrow'), 'optional pyarrow not installed')
    def test_both_years_parquet_long_exports_preserve_bare_nt_codes(self):
        for year in [2016, 2021]:
            source = year_pack(self.root / f'nt-{year}.zip', year=year, state='NT', bare=True)
            out = self.root / f'parquet-{year}'
            files = etl.export_census(year=year, state='NT', source_zip=source,
                                      tables=['G04'], codes=[800], out=out,
                                      long=True, file_format='parquet')
            frame = pd.read_parquet(files['G04'])
            self.assertEqual(frame.columns.tolist(), [f'POA_CODE_{year}', 'variable', 'value'])
            self.assertEqual(frame.iloc[:, 0].tolist(), ['0800', '0800'])

    def test_genuine_data_checker_offline_mode_never_downloads_or_silently_passes(self):
        import runpy
        checks = runpy.run_path(str(ROOT/'tests/check_abs_data.py'))
        with patch.object(etl.requests, 'get', side_effect=AssertionError('network')):
            with self.assertRaisesRegex(RuntimeError, 'Missing offline pack'):
                checks['main'](['--year', '2016', '--offline', '--geographies', 'POA',
                                '--state', 'NSW', '--cache-dir', str(self.root/'absent')])
            with self.assertRaisesRegex(RuntimeError, 'No checks selected'):
                checks['main'](['--year', '2016', '--offline', '--geographies', 'POA',
                                '--state', 'OT', '--cache-dir', str(self.root)])
            page = self.root/'POA2000.html'
            page.write_text('<p>Area code POA2000 People 27,411 Male 50% Median age 30</p>')
            import hashlib
            sidecar = page.with_suffix('.json')
            sidecar.write_text(json.dumps({
                'url': 'https://www.abs.gov.au/census/find-census-data/quickstats/2016/POA2000',
                'file': page.name, 'sha256': hashlib.sha256(page.read_bytes()).hexdigest()}))
            with self.assertRaisesRegex(RuntimeError, 'QuickStats reviewed SHA-256 mismatch'):
                checks['save_reference']((27411, 30, 'POA2000'), self.root,
                                         year=2016, offline=True)
            # Test parser mechanics with an explicit synthetic pin; not real-data evidence.
            with patch.dict(checks['REFERENCE_SHA256'],
                            {(2016, 'POA2000'): hashlib.sha256(page.read_bytes()).hexdigest()}):
                result = checks['save_reference']((27411, 30, 'POA2000'), self.root,
                                                   year=2016, offline=True)
            self.assertTrue(result['offline_reuse'])
            self.assertIsNone(result['retrieved_utc'])
            self.assertIn('/2016/POA2000', result['url'])
            page.write_text(page.read_text() + '<!-- changed -->')
            with self.assertRaisesRegex(RuntimeError, 'QuickStats SHA-256 mismatch'):
                checks['save_reference']((27411, 30, 'POA2000'), self.root,
                                         year=2016, offline=True)
            sidecar.unlink()
            with self.assertRaisesRegex(RuntimeError, 'Missing QuickStats integrity sidecar'):
                checks['save_reference']((27411, 30, 'POA2000'), self.root,
                                         year=2016, offline=True)


if __name__ == '__main__':
    unittest.main()
