"""Offline replay of slice 11 evidence; never downloads or modifies input files.

See README.md alongside this file. Missing range bytes are errors, never zeros.
The original acquisition scripts are historical .txt snapshots, not entry points.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
from html.parser import HTMLParser
import io
import json
from pathlib import Path
import re
from zipfile import ZipFile

BASE = 'https://www.abs.gov.au/census/find-census-data/datapacks/download/'


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def inside(root, name):
    path = (root / name).resolve()
    require(path.is_relative_to(root.resolve()) and path != root.resolve(),
            f'Unsafe evidence path: {name}')
    return path


def load(path):
    return json.loads(path.read_text(encoding='utf-8'))


def evidence_files(root):
    # Exempt only top-level interpreter .pyc files, not arbitrary cache folders.
    # Every other file, including extra HTTP responses, must be listed.
    files = set()
    for path in root.rglob('*'):
        require(not path.is_symlink(), f'Symlinked evidence: {path}')
        relative = path.relative_to(root)
        if (len(relative.parts) == 2 and relative.parts[0] == '__pycache__'
                and path.is_file() and path.suffix == '.pyc'):
            continue
        if path.is_file():
            files.add(path.relative_to(root).as_posix())
    return files


def verify_manifest(root, manifest, expect_sha256=None):
    if expect_sha256 is not None:
        require(re.fullmatch(r'[a-fA-F0-9]{64}', expect_sha256) is not None,
                'Expected SHA-256 must be 64 hexadecimal characters')
        require(digest(manifest) == expect_sha256.lower(), 'Manifest SHA-256 mismatch')
    records = load(manifest)['files']
    require(bool(records), 'Empty manifest')
    for name, record in records.items():
        path = inside(root, name)
        require(path.is_file(), f'Missing manifest file: {name}')
        require(path.stat().st_size == record['bytes'] and digest(path) == record['sha256'],
                f'Manifest mismatch: {name}')
    expected = set(records)
    if manifest.resolve().is_relative_to(root.resolve()):
        expected.add(manifest.resolve().relative_to(root.resolve()).as_posix())
    actual = evidence_files(root)
    require(actual == expected,
            f'Manifest inventory differs: extra={sorted(actual - expected)}, '
            f'missing={sorted(expected - actual)}')
    return len(records)


def create_manifest(root, destination):
    require(not destination.exists(), f'Refusing to overwrite: {destination}')
    require(destination.resolve().parent == root.resolve(), 'Manifest must be at evidence root')
    records = {name: {'bytes': (root / name).stat().st_size, 'sha256': digest(root / name)}
               for name in sorted(evidence_files(root))
               if (root / name).resolve() != destination.resolve()}
    # Exclusive creation: rerunning cannot replace a previously issued manifest.
    with destination.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump({'files': records}, stream, indent=2)
        stream.write('\n')
    return len(records)


class SavedRanges(io.RawIOBase):
    """Seekable view of recorded bytes, with gaps refused explicitly."""
    def __init__(self, root, entries, url):
        self.pos = 0
        self.ranges = []
        sizes = set()
        for entry in entries:
            if entry['url'] != url or entry['status'] != 206:
                continue
            match = re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)',
                                 entry['headers']['Content-Range'])
            require(match is not None, f'Invalid recorded Content-Range: {url}')
            start, end, size = map(int, match.groups())
            data = inside(root, entry['file']).read_bytes()
            require(len(data) == end - start + 1, 'Range size mismatch')
            self.ranges.append((start, end + 1, data))
            sizes.add(size)
        require(len(sizes) == 1, f'Missing or inconsistent ranges: {url}')
        self.size = sizes.pop()

    def readable(self): return True
    def seekable(self): return True
    def tell(self): return self.pos

    def seek(self, offset, whence=0):
        require(whence in (0, 1, 2), 'Invalid seek origin')
        position = offset + (0 if whence == 0 else self.pos if whence == 1 else self.size)
        require(position >= 0, 'Negative seek')
        self.pos = position
        return position

    def readinto(self, target):
        count = min(len(target), max(0, self.size - self.pos))
        done = 0
        while done < count:
            candidates = [(start, end, data) for start, end, data in self.ranges
                          if start <= self.pos < end]
            require(bool(candidates), f'Unrecorded archive bytes at offset {self.pos}')
            start, end, data = max(candidates, key=lambda item: item[1])
            length = min(count - done, end - self.pos)
            target[done:done + length] = data[self.pos - start:self.pos - start + length]
            self.pos += length
            done += length
        return count


class Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, value):
        self.parts.append(value)


def quickstats(root, identifier):
    parser = Text()
    parser.feed((root / f'quickstats-{identifier}.html').read_text(encoding='utf-8'))
    text = re.sub(r'\s+', ' ', ' '.join(parser.parts))
    position = text.find('Area code')
    require(position >= 0, f'Missing QuickStats summary: {identifier}')
    match = re.search(r'\bPeople\s+([0-9,]+)\s+Male.*?\bMedian age\s+([0-9]+)\b',
                      text[position:])
    require(match is not None, f'Cannot parse saved QuickStats: {identifier}')
    return {'people': int(match[1].replace(',', '')), 'median_age': int(match[2])}


def source_cells(archive, geo, state, identifiers):
    name = next(n for n in archive.namelist()
                if n.endswith(f'2016Census_G01_{state}_{geo}.csv'))
    found = {}
    with archive.open(name) as stream:
        for row in csv.DictReader(io.TextIOWrapper(stream, encoding='utf-8-sig')):
            column = next(iter(row))
            if row[column] in identifiers:
                found[row[column]] = int(row['Tot_P_P'])
                if set(found) == set(identifiers):
                    break
    require(set(found) == set(identifiers), f'Missing requested source rows: {identifiers}')
    return found


def decode_readme(body):
    if body.startswith(b'\xef\xbb\xbf'):
        return 'utf-8-sig', body.decode('utf-8-sig')
    try:
        text = body.decode('utf-8')
        return ('ascii' if body.isascii() else 'utf-8'), text
    except UnicodeDecodeError:
        return 'cp1252', body.decode('cp1252')


def replay(root):
    ledger = load(root / 'ledger.json')
    received = 0
    corrected = []
    for original in ledger['requests']:
        entry = dict(original)
        path = inside(root, entry['file'])
        require(path.stat().st_size == entry['bytes_received'], 'Ledger byte mismatch')
        if entry.get('sha256'):
            require(digest(path) == entry['sha256'], 'Ledger digest mismatch')
        received += path.stat().st_size
        if entry['status'] == 404:
            entry['original_error'] = entry.get('error')
            entry['error'] = 'HTTP 404: requested endpoint not found (partial error body retained)'
        corrected.append(entry)
    require(received == ledger['bytes_received'] <= ledger['limit_bytes'],
            'Direct-response total/budget mismatch')
    checks = []
    inventories = []
    cases = [('POA', 'NT', ['POA0800', 'POA0810']),
             ('SA2', 'NSW', ['101021007']), ('LGA', 'NSW', ['LGA17200']),
             ('POA', 'AUS', ['POA2000'])]
    for geo, state, codes in cases:
        url = BASE + f'2016_GCP_{geo}_for_{state}_short-header.zip'
        with ZipFile(io.BufferedReader(SavedRanges(root, ledger['requests'], url))) as archive:
            cells = source_cells(archive, geo, state, codes)
            for code, people in cells.items():
                reference = 'POA2000' if state == 'AUS' else code
                has_reference = (root / f'quickstats-{reference}.html').exists()
                expected = quickstats(root, reference) if has_reference else None
                if expected:
                    require(people == expected['people'], f'Population mismatch: {code}')
                checks.append({'geography': geo, 'state': state, 'code': code,
                               'pack_people': people, 'quickstats': expected,
                               'method': 'stdlib CSV from saved ranges, no runtime reader'})
    with ZipFile(root / '2016_GCP_POA_for_NSW_short-header.zip') as archive:
        inventories.append({'pack': '2016 GCP POA NSW', 'members': archive.namelist()})
        cells = source_cells(archive, 'POA', 'NSW', ['POA2000', 'POA2010'])
        for code, people in cells.items():
            ref = quickstats(root, code) if (root / f'quickstats-{code}.html').exists() else None
            if ref:
                require(people == ref['people'], f'Population mismatch: {code}')
            checks.append({'code': code, 'pack_people': people, 'quickstats': ref})
        revision = []
        for name in archive.namelist():
            if name.lower().endswith('.txt'):
                body = archive.read(name)
                encoding, text = decode_readme(body)
                revision.append({'member': name, 'zip_local_timestamp': archive.getinfo(name).date_time,
                                 'sha256': hashlib.sha256(body).hexdigest(), 'encoding': encoding,
                                 'text': text})
        workbook = 'Metadata/Metadata_2016_GCP_DataPack.xlsx'
        metadata_timestamp = archive.getinfo(workbook).date_time
    url = BASE + '2021_IP_LGA_for_NSW_short-header.zip'
    with ZipFile(io.BufferedReader(SavedRanges(root, ledger['requests'], url))) as archive:
        inventories.append({'pack': '2021 IP LGA NSW', 'members': archive.namelist()})
    return {'network_used': False, 'direct_response_bytes': received,
            'browser_response_bytes': None, 'total_network_budget_certified': False,
            'population_comparisons': [item for item in checks if item['quickstats'] is not None],
            'population_observations': [item for item in checks if item['quickstats'] is None],
            'inventories': inventories,
            'readmes': revision, 'metadata_zip_local_timestamp': metadata_timestamp,
            'corrected_ledger': {**ledger, 'requests': corrected}}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['verify', 'manifest', 'replay'])
    parser.add_argument('--evidence', required=True, type=Path)
    parser.add_argument('--manifest', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--expect-sha256', help='Trusted external hash of the input manifest')
    args = parser.parse_args(argv)
    root = args.evidence.resolve()
    if args.command == 'manifest':
        require(args.manifest is not None, 'manifest requires --manifest (new path)')
        print(f'PASS: created manifest for {create_manifest(root, args.manifest)} files')
    elif args.command == 'verify':
        print(f'PASS: {verify_manifest(root, args.manifest or root / "manifest.json", args.expect_sha256)} manifest files')
    else:
        require(args.output is not None, 'replay requires --output (new directory)')
        require(not args.output.resolve().is_relative_to(root),
                'Replay output must be outside the input evidence directory')
        require(not args.output.exists(), f'Refusing to overwrite: {args.output}')
        verify_manifest(root, args.manifest or root / 'manifest.json', args.expect_sha256)
        result = replay(root)
        args.output.mkdir(parents=True, exist_ok=False)
        with (args.output / 'report.json').open('x', encoding='utf-8', newline='\n') as stream:
            json.dump(result, stream, indent=2)
            stream.write('\n')
        create_manifest(args.output, args.output / 'manifest.json')
        print(f'PASS: offline replay; {len(result["population_comparisons"])} comparisons, '
              f'{len(result["population_observations"])} unreferenced observations; '
              f'new report at {args.output}')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, StopIteration) as exc:
        raise SystemExit(f'ERROR: {exc}') from exc
