"""Supporting 2016 references under the same cap, plus offline evidence summary.

Acquisition requires an explicit --acquire flag and a new output directory.
Offline --verify never makes network requests or rewrites prior evidence.
"""
import argparse
from email.message import Message
import json
from pathlib import Path
import re

from evidence import (Text, create_manifest, digest, quickstats, require, source_cells,
                      verify_manifest, SavedRanges)
from matrix import HTTP, LIMIT, save
import io
from zipfile import ZipFile


def decode_document(body, content_type):
    message = Message()
    message['content-type'] = content_type
    encoding = message.get_content_charset() or 'utf-8'
    return encoding, body.decode(encoding)


def acquire(main, output, main_hash):
    verify_manifest(main, main / 'manifest.json', main_hash)
    used = json.loads((main / 'ledger.json').read_text())['bytes_received']
    require(used < LIMIT, 'No remaining structural budget')
    output.mkdir(parents=True, exist_ok=False)
    http = HTTP(output, limit=LIMIT - used)
    for code in ['POA0810', 'POA2010']:
        _, body = http.get('https://www.abs.gov.au/census/find-census-data/quickstats/2016/' + code)
        (output / f'{code}.html').write_bytes(body)
    url = ('https://www.abs.gov.au/ausstats/abs@.nsf/Lookup/'
           '1270.0.55.001main+features1July%202016')
    headers, body = http.get(url)
    (output / 'asgs-july-2016.html').write_bytes(body)
    encoding, _ = decode_document(body, headers.get('Content-Type', 'text/html'))
    save(output / 'report.json', {'main_phase_bytes': used,
         'supporting_bytes': http.ledger['bytes_received'],
         'combined_bytes': used + http.ledger['bytes_received'],
         'approved_cap_bytes': LIMIT, 'primary_document_decoding': encoding})
    create_manifest(output, output / 'manifest.json')


def verify(main, support, prior, main_hash, support_hash):
    verify_manifest(main, main / 'manifest.json', main_hash)
    verify_manifest(support, support / 'manifest.json', support_hash)
    verify_manifest(prior, prior / 'manifest.json',
                    'f28c9925be9d821c2e3af628cc758816a642fdf8826b7fb6a0c8b9ca40789890')
    matrix_ledger = json.loads((main / 'ledger.json').read_text())
    support_ledger = json.loads((support / 'ledger.json').read_text())
    received = 0
    for entry in support_ledger['requests']:
        body = support / entry['file']
        require(body.stat().st_size == entry['bytes_received'] and digest(body) == entry['sha256'],
                'Supporting response differs')
        received += entry['bytes_received']
    total = matrix_ledger['bytes_received'] + received
    require(received == support_ledger['bytes_received'] and total <= LIMIT,
            'Combined structural cap exceeded or ledger inconsistent')
    require(support_ledger['limit_bytes'] == LIMIT - matrix_ledger['bytes_received'],
            'Supporting ledger must use only the remaining approved budget')
    # Independently parse the retained pages, not the original report fields.
    values = {}
    for code in ['POA0810', 'POA2010']:
        parser = Text()
        parser.feed((support / f'{code}.html').read_text(encoding='utf-8'))
        text = re.sub(r'\s+', ' ', ' '.join(parser.parts))
        position = text.find('Area code')
        require(position >= 0, 'Missing saved QuickStats summary')
        match = re.search(r'\bPeople\s+([0-9,]+)\s+Male.*?\bMedian age\s+([0-9]+)\b',
                          text[position:])
        require(match is not None, 'Cannot parse saved independent reference')
        values[code] = {'people': int(match[1].replace(',', '')), 'median_age': int(match[2])}
    old_entries = json.loads((prior / 'ledger.json').read_text())['requests']
    url = ('https://www.abs.gov.au/census/find-census-data/datapacks/download/'
           '2016_GCP_POA_for_NT_short-header.zip')
    with ZipFile(io.BufferedReader(SavedRanges(prior, old_entries, url))) as archive:
        nt = source_cells(archive, 'POA', 'NT', ['POA0810'])['POA0810']
    with ZipFile(prior / '2016_GCP_POA_for_NSW_short-header.zip') as archive:
        nsw = source_cells(archive, 'POA', 'NSW', ['POA2010'])['POA2010']
    require((nt, nsw) == (values['POA0810']['people'], values['POA2010']['people']),
            'Secondary reference/source populations differ')
    entry = next(e for e in support_ledger['requests'] if '1270.0.55.001' in e['url'])
    body = (support / entry['file']).read_bytes()
    encoding, text = decode_document(body, entry['headers'].get('Content-Type', 'text/html'))
    parser = Text()
    parser.feed(text)
    text = re.sub(r'\s+', ' ', ' '.join(parser.parts))
    require('July 2016' in text and 'second edition of the ASGS' in text,
            'Historical ASGS publication does not confirm the expected edition')
    return {'network_used': False, 'source_population_matches': values,
            'primary_asgs_url': entry['url'], 'primary_asgs_decoding': encoding,
            'primary_asgs_sha256': digest(support / entry['file']),
            'primary_asgs_title': text[:220], 'edition_evidence': 'second edition of the ASGS',
            'combined_response_body_bytes': total, 'approved_cap_bytes': LIMIT}


def main_cli():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--acquire', action='store_true')
    mode.add_argument('--verify', action='store_true')
    parser.add_argument('--matrix', required=True, type=Path)
    parser.add_argument('--matrix-hash', required=True)
    parser.add_argument('--support', required=True, type=Path)
    parser.add_argument('--support-hash')
    parser.add_argument('--prior', type=Path)
    args = parser.parse_args()
    if args.acquire:
        acquire(args.matrix, args.support, args.matrix_hash)
    else:
        require(args.prior is not None and args.support_hash is not None,
                'Offline supporting verification requires --prior and --support-hash')
        print(json.dumps(verify(args.matrix, args.support, args.prior,
                                args.matrix_hash, args.support_hash), indent=2))


if __name__ == '__main__':
    main_cli()
