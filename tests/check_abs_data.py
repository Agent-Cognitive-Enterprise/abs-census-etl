"""Optional genuine ABS 2016/2021 GCP regression check; downloads unless --offline.

Run: .venv/bin/python tests/check_abs_data.py --cache-dir abs_cache
No network is used by the regular unit suite. Reference values below were
independently read from the linked ABS QuickStats pages on 2026-10-20, rather
than generated from this module. ABS confidentiality adjustments mean totals
must not be reconstructed by summing component cells.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import subprocess
from html.parser import HTMLParser
from pathlib import Path
import sys
import tempfile
from datetime import datetime, timezone
from zipfile import ZipFile

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import abs_census_etl as etl

# geo, state, source identifier, people (G01), median age (G02), QuickStats ID
REFERENCES_2021 = [
    ('POA', 'NSW', 'POA2000', 27936, 32, 'POA2000'),
    ('POA', 'NT', 'POA0800', 7149, 33, 'POA0800'),
    ('SA2', 'NSW', '101021007', 4343, 51, '101021007'),
    ('LGA', 'NSW', 'LGA17200', 211632, 34, 'LGA17200'),
]
REFERENCES_2016 = [
    ('POA', 'NSW', 'POA2000', 27411, 30, 'POA2000'),
    ('POA', 'NSW', 'POA2010', 27734, 34, 'POA2010'),
    ('POA', 'NT', 'POA0800', 6464, 32, 'POA0800'),
    ('SA2', 'NSW', '101021007', 3872, 49, '101021007'),
    ('LGA', 'NSW', 'LGA17200', 208374, 32, 'LGA17200'),
]


# Reviewed capture bytes, not hashes supplied by neighboring mutable sidecars.
# 2016: original slice11 evidence plus all-state supporting POA2010 capture.
# 2021: original slices7–10 R1 reference captures. See development guide.
REFERENCE_SHA256 = {
    (2016, 'POA2000'): 'd5ed9e301014e64d192ed036ed65819a4a56628db11c8d1e1b784f932e63992d',
    (2016, 'POA2010'): 'c9b592181ec85c4fc9908b428cb68a919d652a444a58954ac1aa9d4ed1111ca0',
    (2016, 'POA0800'): '0834a1e53bafb94f4348d5c54433b0b32864156ddab6615593fd95c3dc9e1666',
    (2016, '101021007'): 'ca512a30b07ba999415b4b95914158595dba5cdd6ef7de26dcb69e3a6b1646b4',
    (2016, 'LGA17200'): '62051a2dd6f52c2c55126e7565d14759bdbd44001980c3324eb05b000f4969b0',
    (2021, 'POA2000'): '1bbd239c02471de178e53a1c81118ccae6c8ec5aa43d7f6b034cc5b6d0da1610',
    (2021, 'POA0800'): '50c7e65f54887dca0831190f6eec66d0b9765350c71da48220f9ea795be666e4',
    (2021, '101021007'): '033d634fdb80878a5dbb68170170d683729cd9e1472cc4ef80396fd0f96c3962',
    (2021, 'LGA17200'): '05274d56055bf04a5b82f1767e21629492603e79a8053a2d40d2e4aa6ad8880b',
}



def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def save_reference(reference, directory, *, year=2021, offline=False):
    people, age, identifier = reference
    url = f'https://www.abs.gov.au/census/find-census-data/quickstats/{year}/'+identifier
    path = directory/(identifier+'.html')
    sidecar = path.with_suffix('.json')
    if not offline:
        require(not (path.exists() or path.is_symlink() or sidecar.exists() or sidecar.is_symlink()),
                f'Refusing to overwrite reference capture: {path} or {sidecar}; use a fresh directory')
    if offline:
        require(path.is_file(), f'Missing saved QuickStats page: {path}; offline means no download')
        content = path.read_bytes()
        sidecar = path.with_suffix('.json')
        require(sidecar.is_file(), f'Missing QuickStats integrity sidecar: {sidecar}')
        saved = json.loads(sidecar.read_text(encoding='utf-8'))
        require(saved.get('url') == url and saved.get('file') == path.name,
                f'QuickStats sidecar identity mismatch: {sidecar}')
        require(saved.get('sha256') == hashlib.sha256(content).hexdigest(),
                f'QuickStats SHA-256 mismatch: {path}; restore the captured reference')
        require(REFERENCE_SHA256.get((year, identifier)) == hashlib.sha256(content).hexdigest(),
                f'QuickStats reviewed SHA-256 mismatch: {path}; restore the reviewed capture '
                'or independently review a new capture before updating the source pin')
    else:
        response = etl.requests.get(url, timeout=(15, 90),
                                    headers={'User-Agent': 'abs-census-etl-reference-check'})
        response.raise_for_status()
        directory.mkdir(parents=True, exist_ok=True)
        content = response.content
        with path.open('xb') as output:
            output.write(content)

    class Text(HTMLParser):
        def __init__(self):
            super().__init__()
            self.parts = []
        def handle_data(self, text):
            self.parts.append(text)
    parser = Text()
    parser.feed(content.decode('utf-8'))
    text = re.sub(r'\s+', ' ', ' '.join(parser.parts))
    start = text.find('Area code')
    require(start >= 0, f'Cannot find QuickStats summary at {url}; inspect saved HTML')
    summary = text[start:]
    match = re.search(r'\bPeople\s+([0-9,]+)\s+Male.*?\bMedian age\s+([0-9]+)\b', summary)
    require(match is not None, f'Cannot find QuickStats summary at {url}; inspect saved HTML')
    require((int(match[1].replace(',', '')), int(match[2])) == (people, age),
            f'QuickStats summary changed at {url}; investigate before changing expectations')
    evidence = {'url': url, 'retrieved_utc': None if offline else datetime.now(timezone.utc).isoformat(),
                'checked_utc': datetime.now(timezone.utc).isoformat(), 'offline_reuse': offline,
                'file': path.name, 'sha256': hashlib.sha256(content).hexdigest(),
                'people': people, 'median_age': age}
    if not offline:
        with sidecar.open('x', encoding='utf-8') as output:
            output.write(json.dumps(evidence, indent=2)+'\n')
    return evidence


def check_pack(geo, state, code, cache, out, *, reference=None, all_tables=False,
               reference_dir=None, year=2021, offline=False):
    require(not (offline and reference is not None and reference_dir is None),
            'Offline curated checks require --reference-dir with reviewed QuickStats captures')
    url = etl.datapack_url(geo, state, year=year)
    source = cache/Path(url).name
    require(not offline or source.is_file(), f'Missing offline pack: {source}; no download attempted')
    source = etl._resolve_source(geo=geo, state=state, source_zip=None, cache_dir=cache, year=year)
    with source.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    inventory = etl.list_census_tables(geo=geo, state=state, source_zip=source, year=year)
    count = etl._vintage(year)["table_count"]
    require(len(inventory) == count, f'{geo}/{state}: expected {count} logical GCP tables')
    table_ids = inventory['table'].tolist() if all_tables else ['G01', 'G02', 'G04']
    data = etl.load_census_tables(geo=geo, state=state, source_zip=source,
                                   tables=table_ids, codes=[code], year=year)
    column = ({'SA1': 'SA1_7DIGITCODE_2016', 'SA2': 'SA2_MAINCODE_2016'}.get(
        geo, f'{geo}_CODE_2016') if year == 2016 else f'{geo}_CODE_2021')
    for table, frame in data.items():
        require(frame[column].tolist() == [code], f'{geo}/{table}: code changed')
    if reference:
        people, median, quickstats = reference
        require(data['G01']['Tot_P_P'].iloc[0] == people, f'{geo}/{code}: population mismatch')
        require(data['G02']['Median_age_persons'].iloc[0] == median,
                f'{geo}/{code}: median age mismatch')
    # Independent stdlib CSV extraction checks joined G04 cells against both
    # original parts. It tests fidelity to source, not external demographic truth.
    with ZipFile(source) as archive:
        for member in etl.find_members(archive, 'G04', geo, state, year=year):
            rows = csv.DictReader(io.StringIO(archive.read(member).decode('utf-8-sig')))
            raw = next(row for row in rows if row[column] == code)
            for name, value in raw.items():
                if name != column:
                    actual = data['G04'][name].iloc[0]
                    require(pd.isna(actual) if value.strip().lower() in etl.MISSING
                            else actual == float(value), f'{geo}/{code}/{name}: split cell mismatch')
    # Default discovery must cover every real table, including lettered profiles.
    metadata = etl.describe_census(geo=geo, state=state, source_zip=source, year=year)
    expected_variables = etl._vintage(year)["variable_count"]
    require(len(metadata) == expected_variables, f'{year}/{geo}: variable inventory differs')
    with ZipFile(source) as archive:
        for table in inventory['table']:
            for member in etl.find_members(archive, table, geo, state, year=year):
                part = Path(member).name.split('_')[1].upper()
                with archive.open(member) as stream:
                    header = next(csv.reader(io.TextIOWrapper(stream, encoding='utf-8-sig')))
                expected = {name.strip() for name in header}-{column}
                found = set(metadata.loc[metadata['datapack_table'].eq(part), 'variable'])
                require(found == expected, f'{geo}/{part}: metadata/header mismatch: '
                        f'missing={sorted(expected-found)}, extra={sorted(found-expected)}')
    if geo == 'POA' and state == 'NSW':
        result = subprocess.run([sys.executable, str(ROOT/'abs_census_etl.py'),
            '--geo', 'POA', '--state', 'NSW', '--year', str(year), '--zip', str(source),
            '--describe', '--search', 'median_age'], capture_output=True, text=True, timeout=60)
        require(result.returncode == 0 and 'Median_age_persons' in result.stdout,
                f'README default discovery failed: {result.stdout}{result.stderr}')
        print('PASS: README default --describe --search median_age on genuine POA NSW', flush=True)
    reference_evidence = (save_reference(reference, reference_dir, year=year, offline=offline)
                          if reference is not None and reference_dir is not None else None)
    require(metadata.set_index('variable').loc['Tot_P_P', 'long_name']
            == 'Total_Persons_Persons', f'{geo}: long descriptor mismatch')
    files = etl.export_census(geo=geo, state=state, source_zip=source,
                              tables=['G01', 'G04'], codes=[code], out=out, year=year)
    manifest = json.loads((out/f'{year}_GCP_{geo}_{state}_manifest.json').read_text())
    require(manifest['source_sha256'] == digest, 'Top-level source hash mismatch')
    for table, file in files.items():
        require(manifest['tables'][table]['source_sha256'] == digest, 'Table hash mismatch')
        require(pd.read_csv(file, dtype={column: 'string'})[column].tolist() == [code],
                'CSV code round-trip mismatch')
    print(f'PASS: {geo}/{state}/{code}: {len(data)} tables checked; sha256={digest}', flush=True)
    return {'census_year': year, 'geography': geo, 'state': state, 'code': code, 'source_url': url,
            'source_sha256': digest, 'tables_checked': table_ids, 'metadata_variables': len(metadata),
            'metadata_parts': sorted(metadata['datapack_table'].unique().tolist()),
            'reference_evidence': reference_evidence,
            'reference_url': (f'https://www.abs.gov.au/census/find-census-data/quickstats/{year}/'
                              + reference[2]) if reference else None,
            'reference_people': reference[0] if reference else None,
            'reference_median_age': reference[1] if reference else None}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--year', type=int, choices=[2016, 2021], default=2021)
    parser.add_argument('--offline', action='store_true', help='Require cached packs/pages; never download')
    parser.add_argument('--geographies', nargs='+', help='Limit checks to selected geographies')
    parser.add_argument('--state', choices=sorted(etl.VALID_STATES), help='Limit references/extra packs to one state')
    parser.add_argument('--cache-dir', type=Path,
                        help='Retain downloaded ZIPs; default uses a disposable cache')
    parser.add_argument('--report', type=Path, help='Write JSON provenance report')
    parser.add_argument('--reference-dir', type=Path,
                        help='Retrieve and retain full QuickStats HTML and provenance for the selected references')
    parser.add_argument('--all-tables', action='store_true',
                        help='Also load every table for each checked pack')
    parser.add_argument('--all-geographies', action='store_true',
                        help='Also check remaining supported geographies with source-cell checks')
    args = parser.parse_args(argv)
    require(not args.offline or args.reference_dir is not None,
            '--offline requires --reference-dir with reviewed QuickStats captures')
    selected_geos = None if args.geographies is None else {
        etl._validate_geography(g, 'NSW', year=args.year)[0] for g in args.geographies}
    references = REFERENCES_2016 if args.year == 2016 else REFERENCES_2021
    with tempfile.TemporaryDirectory(prefix='abs-real-data-') as td:
        temporary = Path(td)
        cache = args.cache_dir if args.cache_dir is not None else temporary/'cache'
        cache.mkdir(parents=True, exist_ok=True)
        cases = [(g, s, c, (people, age, ref)) for g, s, c, people, age, ref in references
                 if (selected_geos is None or g in selected_geos)
                 and (args.state is None or s == args.state)]
        if args.all_geographies:
            state = args.state or 'NSW'
            geos = set(etl._vintage(args.year)['columns']) if selected_geos is None else selected_geos
            for geo in sorted(geos - {g for g, s, c, r in cases if s == state}):
                source = cache/Path(etl.datapack_url(geo, state, year=args.year)).name
                require(not args.offline or source.is_file(), f'Missing offline pack: {source}')
                source = etl._resolve_source(geo=geo, state=state, source_zip=None,
                                             cache_dir=cache, year=args.year)
                with ZipFile(source) as archive:
                    member = etl.find_members(archive, 'G01', geo, state, year=args.year)[0]
                    rows = csv.DictReader(io.StringIO(archive.read(member).decode('utf-8-sig')))
                    code = next(rows)[etl._code_column(geo, args.year)]
                cases.append((geo, state, code, None))
        require(bool(cases), 'No checks selected; use a reference geography/state or --all-geographies')
        results = [check_pack(g, s, c, cache, temporary/f'{g}-{s}',
                               reference=ref, all_tables=args.all_tables, reference_dir=args.reference_dir,
                               year=args.year, offline=args.offline)
                   for g, s, c, ref in cases]
        report = {'checked_utc': datetime.now(timezone.utc).isoformat(),
                  'runtime_sha256': hashlib.sha256((ROOT/'abs_census_etl.py').read_bytes()).hexdigest(),
                  'reference_checked_date': '2026-10-20', 'results': results}
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    print('PASS: genuine ABS checks complete; temporary exports removed')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, etl.requests.RequestException) as error:
        print(f'ERROR: {error}', file=sys.stderr)
        raise SystemExit(1)
