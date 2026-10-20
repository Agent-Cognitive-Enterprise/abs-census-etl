"""Optional genuine ABS 2021 regression check; downloads packs unless cached.

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
REFERENCES = [
    ('POA', 'NSW', 'POA2000', 27936, 32, 'POA2000'),
    ('POA', 'NT', 'POA0800', 7149, 33, 'POA0800'),
    ('SA2', 'NSW', '101021007', 4343, 51, '101021007'),
    ('LGA', 'NSW', 'LGA17200', 211632, 34, 'LGA17200'),
]


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def save_reference(reference, directory):
    people, age, identifier = reference
    url = 'https://www.abs.gov.au/census/find-census-data/quickstats/2021/'+identifier
    response = etl.requests.get(url, timeout=(15, 90),
                                headers={'User-Agent': 'abs-census-etl-reference-check'})
    response.raise_for_status()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory/(identifier+'.html')
    path.write_bytes(response.content)
    class Text(HTMLParser):
        def __init__(self):
            super().__init__()
            self.parts = []
        def handle_data(self, text):
            self.parts.append(text)
    parser = Text()
    parser.feed(response.text)
    text = re.sub(r'\s+', ' ', ' '.join(parser.parts))
    start = text.find('Area code')
    require(start >= 0, f'Cannot find QuickStats summary at {url}; inspect saved HTML')
    summary = text[start:]
    match = re.search(r'\bPeople\s+([0-9,]+)\s+Male.*?\bMedian age\s+([0-9]+)\b', summary)
    require(match is not None, f'Cannot find QuickStats summary at {url}; inspect saved HTML')
    require((int(match[1].replace(',', '')), int(match[2])) == (people, age),
            f'QuickStats summary changed at {url}; investigate before changing expectations')
    evidence = {'url': url, 'retrieved_utc': datetime.now(timezone.utc).isoformat(),
                'file': path.name, 'sha256': hashlib.sha256(response.content).hexdigest(),
                'people': people, 'median_age': age}
    path.with_suffix('.json').write_text(json.dumps(evidence, indent=2)+'\n', encoding='utf-8')
    return evidence


def check_pack(geo, state, code, cache, out, *, reference=None, all_tables=False,
               reference_dir=None):
    url = etl.datapack_url(geo, state)
    source = cache/Path(url).name
    etl.download_if_needed(url, source)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    inventory = etl.list_census_tables(geo=geo, state=state, source_zip=source)
    require(len(inventory) == 62, f'{geo}/{state}: expected 62 logical GCP tables')
    table_ids = inventory['table'].tolist() if all_tables else ['G01', 'G02', 'G04']
    data = etl.load_census_tables(geo=geo, state=state, source_zip=source,
                                   tables=table_ids, codes=[code])
    column = f'{geo}_CODE_2021'
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
        for member in etl.find_members(archive, 'G04', geo, state):
            rows = csv.DictReader(io.StringIO(archive.read(member).decode('utf-8-sig')))
            raw = next(row for row in rows if row[column] == code)
            for name, value in raw.items():
                if name != column:
                    actual = data['G04'][name].iloc[0]
                    require(pd.isna(actual) if value.strip().lower() in etl.MISSING
                            else actual == float(value), f'{geo}/{code}/{name}: split cell mismatch')
    # Default discovery must cover every real table, including lettered profiles.
    metadata = etl.describe_census(geo=geo, state=state, source_zip=source)
    with ZipFile(source) as archive:
        for table in inventory['table']:
            for member in etl.find_members(archive, table, geo, state):
                part = etl.MEMBER_RE.fullmatch(Path(member).name).group(1).upper()
                with archive.open(member) as stream:
                    header = next(csv.reader(io.TextIOWrapper(stream, encoding='utf-8-sig')))
                expected = {name.strip() for name in header}-{column}
                found = set(metadata.loc[metadata['datapack_table'].eq(part), 'variable'])
                require(found == expected, f'{geo}/{part}: metadata/header mismatch: '
                        f'missing={sorted(expected-found)}, extra={sorted(found-expected)}')
    if geo == 'POA' and state == 'NSW':
        result = subprocess.run([sys.executable, str(ROOT/'abs_census_etl.py'),
            '--geo', 'POA', '--state', 'NSW', '--zip', str(source),
            '--describe', '--search', 'median_age'], capture_output=True, text=True, timeout=60)
        require(result.returncode == 0 and 'Median_age_persons' in result.stdout,
                f'README default discovery failed: {result.stdout}{result.stderr}')
        print('PASS: README default --describe --search median_age on genuine POA NSW', flush=True)
    reference_evidence = (save_reference(reference, reference_dir)
                          if reference is not None and reference_dir is not None else None)
    require(metadata.set_index('variable').loc['Tot_P_P', 'long_name']
            == 'Total_Persons_Persons', f'{geo}: long descriptor mismatch')
    files = etl.export_census(geo=geo, state=state, source_zip=source,
                              tables=['G01', 'G04'], codes=[code], out=out)
    manifest = json.loads((out/f'2021_GCP_{geo}_{state}_manifest.json').read_text())
    require(manifest['source_sha256'] == digest, 'Top-level source hash mismatch')
    for table, file in files.items():
        require(manifest['tables'][table]['source_sha256'] == digest, 'Table hash mismatch')
        require(pd.read_csv(file, dtype={column: 'string'})[column].tolist() == [code],
                'CSV code round-trip mismatch')
    print(f'PASS: {geo}/{state}/{code}: {len(data)} tables checked; sha256={digest}', flush=True)
    return {'geography': geo, 'state': state, 'code': code, 'source_url': url,
            'source_sha256': digest, 'tables_checked': table_ids, 'metadata_variables': len(metadata),
            'metadata_parts': sorted(metadata['datapack_table'].unique().tolist()),
            'reference_evidence': reference_evidence,
            'reference_url': ('https://www.abs.gov.au/census/find-census-data/quickstats/2021/'
                              + reference[2]) if reference else None,
            'reference_people': reference[0] if reference else None,
            'reference_median_age': reference[1] if reference else None}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache-dir', type=Path,
                        help='Retain downloaded ZIPs; default uses a disposable cache')
    parser.add_argument('--report', type=Path, help='Write JSON provenance report')
    parser.add_argument('--reference-dir', type=Path,
                        help='Retrieve and retain full QuickStats HTML and provenance for all four references')
    parser.add_argument('--all-tables', action='store_true',
                        help='Also load every table for each checked pack')
    parser.add_argument('--all-geographies', action='store_true',
                        help='Also check remaining supported geographies with source-cell checks')
    args = parser.parse_args(argv)
    with tempfile.TemporaryDirectory(prefix='abs-real-data-') as td:
        temporary = Path(td)
        cache = args.cache_dir if args.cache_dir is not None else temporary/'cache'
        cache.mkdir(parents=True, exist_ok=True)
        cases = [(g, s, c, (people, age, ref)) for g, s, c, people, age, ref in REFERENCES]
        if args.all_geographies:
            for geo in sorted(etl.VALID_GEOS-{'POA', 'SA2', 'LGA'}):
                source = cache/Path(etl.datapack_url(geo, 'NSW')).name
                etl.download_if_needed(etl.datapack_url(geo, 'NSW'), source)
                with ZipFile(source) as archive:
                    member = etl.find_members(archive, 'G01', geo, 'NSW')[0]
                    rows = csv.DictReader(io.StringIO(archive.read(member).decode('utf-8-sig')))
                    code = next(rows)[f'{geo}_CODE_2021']
                cases.append((geo, 'NSW', code, None))
        results = [check_pack(g, s, c, cache, temporary/f'{g}-{s}',
                               reference=ref, all_tables=args.all_tables, reference_dir=args.reference_dir)
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
    raise SystemExit(main())
