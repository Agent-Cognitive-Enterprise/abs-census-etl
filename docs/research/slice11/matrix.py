"""Bounded 2016 GCP structural survey. Explicit new output; no runtime support.

Every new HTTP response body, including errors/redirects, is saved and counted.
This command does not download complete packs when range requests are ignored.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import re
from urllib.parse import urljoin, urlparse
from zipfile import ZipFile

import requests

from evidence import (BASE, SavedRanges, create_manifest, decode_readme, digest,
                      require, verify_manifest)

LIMIT = 128 * 1024 * 1024
STATES = ('NSW', 'VIC', 'QLD', 'SA', 'WA', 'TAS', 'NT', 'ACT', 'OT', 'AUS')
COLUMNS = {'POA': 'POA_CODE_2016', 'SA1': 'SA1_7DIGITCODE_2016',
           'SA2': 'SA2_MAINCODE_2016', 'SA3': 'SA3_CODE_2016',
           'SA4': 'SA4_CODE_2016', 'LGA': 'LGA_CODE_2016',
           'SED': 'SED_CODE_2016', 'CED': 'CED_CODE_2016',
           'SSC': 'SSC_CODE_2016', 'STE': 'STE_CODE_2016'}
MEMBER = re.compile(r'(\d{4})Census_([A-Z]\d{2}[A-Z]?)_([A-Z]+)_([A-Z0-9]+)\.csv', re.I)


def save(path, value):
    temporary = path.with_suffix(path.suffix + '.part')
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n',
                         encoding='utf-8')
    temporary.replace(path)


class TransferError(ValueError):
    pass


class BudgetError(TransferError):
    pass


class HTTP:
    def __init__(self, root, *, limit=LIMIT, session=None):
        self.root, self.limit = root, limit
        self.session = session or requests.Session()
        self.session.headers.update({'User-Agent': 'abs-census-etl-slice11-matrix',
                                     'Accept-Encoding': 'identity'})
        self.ledger = {'limit_bytes': limit, 'bytes_received': 0, 'requests': []}
        (root / 'responses').mkdir()
        save(root / 'ledger.json', self.ledger)

    def get(self, url, *, start=None, end=None, max_body=1024 * 1024):
        for redirect in range(6):
            require(urlparse(url).scheme == 'https', 'Only HTTPS sources are permitted')
            remaining = self.limit - self.ledger['bytes_received']
            if remaining <= 0:
                raise BudgetError('Approved response-body cap reached')
            number = len(self.ledger['requests'])
            path = self.root / 'responses' / f'{number:05d}.bin'
            entry = {'url': url, 'utc': datetime.now(timezone.utc).isoformat(),
                     'requested_range': None if start is None else f'bytes={start}-{end}',
                     'file': str(path.relative_to(self.root)), 'bytes_received': 0}
            self.ledger['requests'].append(entry)
            headers = {} if start is None else {'Range': entry['requested_range']}
            path.touch(exist_ok=False)
            try:
                with self.session.get(url, headers=headers, stream=True,
                                      allow_redirects=False, timeout=(15, 40)) as response:
                    entry.update(status=response.status_code, headers=dict(response.headers))
                    is_range = start is not None and response.status_code == 206
                    expected = end - start + 1 if is_range else None
                    allowance = min(remaining, expected if is_range else max_body)
                    with path.open('wb') as output:
                        while entry['bytes_received'] < allowance:
                            count = min(16384, allowance - entry['bytes_received'])
                            # Count encoded response-body bytes, not decoded text. No
                            # iterator prefetch beyond the remaining approved cap.
                            chunk = response.raw.read(count, decode_content=False)
                            if not chunk:
                                break
                            output.write(chunk)
                            entry['bytes_received'] += len(chunk)
                            self.ledger['bytes_received'] += len(chunk)
                    entry['sha256'] = digest(path)
                    body = path.read_bytes()
                    length = response.headers.get('Content-Length')
                    entry['body_complete'] = (len(body) == int(length) if length is not None
                                               else len(body) < allowance)
                    if 300 <= response.status_code < 400:
                        require(redirect < 5, 'Too many source redirects')
                        url = urljoin(url, response.headers['Location'])
                        continue
                    if response.status_code >= 400:
                        raise TransferError(f'HTTP {response.status_code}: {url}')
                    if start is not None:
                        require(response.status_code == 206,
                                f'Server refused bounded range (HTTP {response.status_code})')
                        match = re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)',
                                             response.headers.get('Content-Range', ''))
                        require(match is not None and tuple(map(int, match.group(1, 2)))
                                == (start, end), 'Unexpected Content-Range')
                        if len(body) != expected:
                            if self.ledger['bytes_received'] == self.limit:
                                raise BudgetError('Approved cap reached during range')
                            raise TransferError('Truncated range response')
                    else:
                        require(entry['body_complete'], 'Supporting source exceeds bounded response limit')
                    return response.headers, body
            except BaseException as error:
                entry['error'] = f'{type(error).__name__}: {error}'
                raise
            finally:
                entry['sha256'] = digest(path)
                save(self.root / 'ledger.json', self.ledger)
        raise TransferError('Source redirects exhausted')


class Remote(io.RawIOBase):
    BLOCK = 65536

    def __init__(self, http, url):
        self.http, self.url, self.pos, self.blocks = http, url, 0, {}
        headers, _ = http.get(url, start=0, end=0, max_body=16384)
        self.size = int(headers['Content-Range'].split('/')[-1])

    def readable(self): return True
    def seekable(self): return True
    def tell(self): return self.pos

    def seek(self, offset, whence=0):
        require(whence in (0, 1, 2), 'Invalid seek origin')
        self.pos = offset + (0 if whence == 0 else self.pos if whence == 1 else self.size)
        require(self.pos >= 0, 'Negative archive seek')
        return self.pos

    def readinto(self, target):
        count = min(len(target), max(0, self.size - self.pos))
        done = 0
        while done < count:
            key = self.pos // self.BLOCK
            if key not in self.blocks:
                start = key * self.BLOCK
                end = min(start + self.BLOCK, self.size) - 1
                _, self.blocks[key] = self.http.get(self.url, start=start, end=end)
            offset = self.pos % self.BLOCK
            chunk = self.blocks[key][offset:offset + count - done]
            require(bool(chunk), 'Unexpected empty archive block')
            target[done:done + len(chunk)] = chunk
            self.pos += len(chunk)
            done += len(chunk)
        return count


def inspect(archive, geo, state):
    names = archive.namelist()
    members = []
    for name in names:
        match = MEMBER.fullmatch(Path(name).name)
        if match:
            require((match[1], match[2][0].upper(), match[3].upper(), match[4].upper())
                    == ('2016', 'G', state, geo), f'Conflicting data member: {name}')
            members.append((match[2].upper(), name))
    require(members and len({part for part, _ in members}) == len(members),
            'Missing or duplicate GCP data parts')
    g01 = [name for part, name in members if part == 'G01']
    require(len(g01) == 1, 'Expected one G01 member')
    metadata = [n for n in names if Path(n).name == 'Metadata_2016_GCP_DataPack.xlsx']
    require(len(metadata) == 1 and archive.getinfo(metadata[0]).file_size > 0,
            'Missing/empty GCP metadata workbook')
    with archive.open(g01[0]) as source:
        rows = csv.reader(io.TextIOWrapper(source, encoding='utf-8-sig'))
        header = next(rows)
        require(header[0] == COLUMNS[geo] and header.count(COLUMNS[geo]) == 1,
                f'Unexpected G01 code column: {header[:2]}')
        samples = []
        for _, row in zip(range(3), rows):
            require(len(row) == len(header), 'G01 sample/header width differs')
            samples.append(row[0])
    readmes = []
    for name in names:
        if name.lower().startswith('readme/') and name.lower().endswith('.txt'):
            require(archive.getinfo(name).file_size <= 200000, 'Unexpectedly large README')
            content = archive.read(name)
            encoding, text = decode_readme(content)
            readmes.append({'member': name, 'sha256': hashlib.sha256(content).hexdigest(),
                            'decoding': encoding, 'cp1252_is_inferred': encoding == 'cp1252',
                            'text': text, 'zip_local_timestamp': archive.getinfo(name).date_time})
    require(any(Path(r['member']).name == 'AboutDatpacks_readme.txt' for r in readmes),
            'Missing official availability README')
    return {'status': 'verified-present', 'code_column': header[0], 'sample_codes': samples,
            'g01_empty': not samples, 'csv_parts': len(members),
            'members': names, 'readmes': readmes,
            'threshold': 'structural: central directory, G01 header/samples, metadata names, readmes',
            'full_zip_sha256': None}


def survey(output, prior):
    # Prior bytes are reused only after external manifest-anchor verification.
    verify_manifest(prior, prior / 'manifest.json',
                    'f28c9925be9d821c2e3af628cc758816a642fdf8826b7fb6a0c8b9ca40789890')
    output.mkdir(parents=True, exist_ok=False)
    http = HTTP(output)
    previous = json.loads((prior / 'results.json').read_text())
    old_entries = json.loads((prior / 'ledger.json').read_text())['requests']
    prior_pairs = {(item['geography'], item['state']) for item in previous}
    report = {'profile': 'GCP', 'year': 2016, 'budget_bytes': LIMIT,
              'gate_passed': False, 'cells': [], 'phase': 'fresh measured 128 MiB; prior reuse separate'}
    for geo in COLUMNS:
        for state in STATES:
            url = BASE + f'2016_GCP_{geo}_for_{state}_short-header.zip'
            cell = {'geography': geo, 'state': state, 'url': url}
            try:
                if (geo, state) in prior_pairs:
                    if (geo, state) == ('POA', 'NSW'):
                        source = prior / '2016_GCP_POA_for_NSW_short-header.zip'
                        with ZipFile(source) as archive:
                            cell.update(inspect(archive, geo, state))
                        cell['full_zip_sha256'] = digest(source)
                    else:
                        with ZipFile(io.BufferedReader(SavedRanges(prior, old_entries, url))) as archive:
                            cell.update(inspect(archive, geo, state))
                    cell['evidence_origin'] = 'offline reuse; acquired 2026-10-20, original manifest f28c9925...'
                else:
                    source = Remote(http, url)
                    with ZipFile(io.BufferedReader(source)) as archive:
                        cell.update(inspect(archive, geo, state))
                    cell.update(evidence_origin='new ledgered HTTP ranges', archive_size=source.size)
            except BudgetError:
                cell.update(status='unresolved', error='Budget exhausted; survey stopped')
                report['cells'].append(cell)
                save(output / 'matrix.json', report)
                raise
            except Exception as error:
                cell.update(status='unresolved', error=f'{type(error).__name__}: {error}')
            report['cells'].append(cell)
            report['new_response_body_bytes'] = http.ledger['bytes_received']
            save(output / 'matrix.json', report)
            print(f'{geo}/{state}: {cell["status"]} {cell.get("error", "")} '
                  f'({http.ledger["bytes_received"]} bytes)', flush=True)
    report['gate_passed'] = all(c['status'] in {'verified-present', 'confirmed-absent/inapplicable'}
                                for c in report['cells']) and len(report['cells']) == 100
    save(output / 'matrix.json', report)
    create_manifest(output, output / 'manifest.json')
    return report


# Complete G01 Tot_P_P rows of the Other Territories packs whose rows are mostly
# special codes. Names come from 2016Census_geog_desc_1st_and_2nd_release.xlsx in
# the hash-anchored POA NSW archive: CED994/SED99494/LGA99499/SA4 999 "No usual
# address (OT)", the x97 codes "Migratory - Offshore - Shipping (OT)", SED99191
# "Unclassified (OT)", LGA99399 "Unincorp. Other Territories", SA4 901 "Other
# Territories", POA9494/POA9797 the national "(Aust.)" equivalents.
OT_G01_ROWS = {
    'CED': {'CED994': 55, 'CED997': 6},
    'SED': {'SED99191': 4523, 'SED99494': 55, 'SED99797': 6},
    'LGA': {'LGA99399': 4523, 'LGA99499': 55, 'LGA99799': 6},
    'POA': {'POA2899': 1748, 'POA6798': 1843, 'POA6799': 544,
            'POA9494': 45182, 'POA9797': 1187},
    'SA4': {'901': 4523, '997': 6, '999': 55},
}


def complete_g01(archive, geo, state):
    """Every G01 code's Tot_P_P; reading to EOF makes zipfile check the CRC."""
    name = next(n for n in archive.namelist()
                if Path(n).name == f'2016Census_G01_{state}_{geo}.csv')
    with archive.open(name) as source:
        rows = [(row[COLUMNS[geo]], int(row['Tot_P_P'])) for row in
                csv.DictReader(io.TextIOWrapper(source, encoding='utf-8-sig'))]
    found = dict(rows)
    require(len(found) == len(rows), f'Duplicate G01 codes: {geo}/{state}')
    return found


def verify_ot_rows(root, entries):
    observed = {}
    for geo, expected in OT_G01_ROWS.items():
        url = BASE + f'2016_GCP_{geo}_for_OT_short-header.zip'
        with ZipFile(io.BufferedReader(SavedRanges(root, entries, url))) as archive:
            observed[geo] = complete_g01(archive, geo, 'OT')
        require(observed[geo] == expected, f'OT G01 rows differ: {geo}')
    return observed


def data_parts(members):
    return sorted(match[2].upper() for match in
                  (MEMBER.fullmatch(Path(name).name) for name in members) if match)


def uniform_inventory(cells, reference=('POA', 'NSW')):
    """Every cell must list the reference cell's exact data-part IDs."""
    expected = data_parts(next(c for c in cells
                               if (c['geography'], c['state']) == reference)['members'])
    for cell in cells:
        require(data_parts(cell['members']) == expected,
                f'Data-part inventory differs: {cell["geography"]}/{cell["state"]}')
    return expected


def verify_survey(root, prior, expected_hash=None):
    verify_manifest(root, root / 'manifest.json', expected_hash)
    verify_manifest(prior, prior / 'manifest.json',
                    'f28c9925be9d821c2e3af628cc758816a642fdf8826b7fb6a0c8b9ca40789890')
    report = json.loads((root / 'matrix.json').read_text())
    ledger = json.loads((root / 'ledger.json').read_text())
    old = json.loads((prior / 'ledger.json').read_text())
    total = 0
    for entry in ledger['requests']:
        path = root / entry['file']
        require(path.stat().st_size == entry['bytes_received']
                and digest(path) == entry['sha256'], 'Matrix response ledger mismatch')
        total += entry['bytes_received']
    require(total == ledger['bytes_received'] == report['new_response_body_bytes']
            and total <= LIMIT == ledger['limit_bytes'], 'Matrix budget mismatch')
    pairs = [(cell['geography'], cell['state']) for cell in report['cells']]
    require(len(pairs) == 100 and set(pairs) == {(g, s) for g in COLUMNS for s in STATES},
            'Incomplete or duplicate matrix cells')
    for cell in report['cells']:
        geo, state = cell['geography'], cell['state']
        require(cell['status'] == 'verified-present', 'Unresolved/absent cell requires manual evidence review')
        url = BASE + f'2016_GCP_{geo}_for_{state}_short-header.zip'
        require(cell['url'] == url, 'Cell source URL differs')
        if cell['evidence_origin'].startswith('offline reuse'):
            if (geo, state) == ('POA', 'NSW'):
                source = prior / '2016_GCP_POA_for_NSW_short-header.zip'
            else:
                source = io.BufferedReader(SavedRanges(prior, old['requests'], url))
        else:
            source = io.BufferedReader(SavedRanges(root, ledger['requests'], url))
        with ZipFile(source) as archive:
            actual = json.loads(json.dumps(inspect(archive, geo, state)))
        for key, value in actual.items():
            # The complete local POA archive alone has a full ZIP hash.
            if key == 'full_zip_sha256' and (geo, state) == ('POA', 'NSW'):
                value = digest(prior / '2016_GCP_POA_for_NSW_short-header.zip')
            require(cell.get(key) == value, f'Replayed cell differs: {geo}/{state}/{key}')
    require(report['gate_passed'] is True, 'Matrix does not claim a passed gate')
    # POA NSW is the complete archive whose metadata matched all part headers.
    parts = uniform_inventory(report['cells'])
    tables = {part[:3] for part in parts}
    require((len(parts), len(tables)) == (110, 59), 'Reference inventory differs from record')
    ot_rows = verify_ot_rows(root, ledger['requests'])
    return {'cells_replayed': len(pairs), 'new_response_body_bytes': total,
            'requests': len(ledger['requests']), 'network_used': False,
            'uniform_data_parts': len(parts), 'logical_tables': len(tables),
            'ot_g01_rows': ot_rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--output', type=Path, help='Acquire into a new directory')
    mode.add_argument('--verify', type=Path, help='Offline replay of an existing survey')
    parser.add_argument('--expect-sha256', help='External matrix-manifest hash for verification')
    parser.add_argument('--prior', required=True, type=Path)
    args = parser.parse_args()
    if args.verify:
        result = verify_survey(args.verify.resolve(), args.prior.resolve(), args.expect_sha256)
        print('PASS:', json.dumps(result))
        return 0
    report = survey(args.output.resolve(), args.prior.resolve())
    print('GATE', report['gate_passed'], 'BYTES', report['new_response_body_bytes'])
    return 0 if report['gate_passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
