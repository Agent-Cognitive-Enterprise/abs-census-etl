"""Offline real-v0.2.0 writer compatibility check; requires that Git tag locally.

Run with project Python: tests/check_compat.py [--report PATH]. No network,
checkout mutation or shared-environment installation is performed. Source archives
without the tag fail clearly; fetch the tag in a checkout before running this check.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import abs_census_etl as current
try:
    from tests.test_year_coverage import year_pack
except ModuleNotFoundError:
    from test_year_coverage import year_pack


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


OLD_SOURCE_SHA256 = '31ebf8a3d553fca8e81761ffceded416178a547ec684d5ca978841e93a795869'


def read_old_source(root=ROOT):
    root = Path(root).resolve()
    checkout = subprocess.run(['git', '-C', str(root), 'rev-parse', '--show-toplevel'],
                              capture_output=True, text=True)
    require(checkout.returncode == 0 and Path(checkout.stdout.strip()).resolve() == root,
            'Compatibility check needs a Git checkout containing the v0.2.0 tag. '
            'An extracted source archive or a parent repository is insufficient.')
    result = subprocess.run(['git', '-C', str(root), 'show', 'v0.2.0:abs_census_etl.py'],
                            capture_output=True)
    require(result.returncode == 0,
            'Compatibility check needs a Git checkout containing the v0.2.0 tag. '
            'Fetch that tag, then rerun; an extracted source archive alone is insufficient.')
    require(hashlib.sha256(result.stdout).hexdigest() == OLD_SOURCE_SHA256,
            'v0.2.0 source SHA-256 differs from the reviewed published writer; '
            'restore the authentic tag before running compatibility checks.')
    return result.stdout


def revised_pack(path, text):
    year_pack(path, year=2021)
    with ZipFile(path) as archive:
        members = {name: archive.read(name) for name in archive.namelist()}
    with ZipFile(path, 'w', compression=ZIP_DEFLATED) as archive:
        for name, body in members.items():
            archive.writestr(name, body)
        archive.writestr('Readme/Summary_of_Changes.txt', text)
    return path


def run_check(*, report=None, fixture=None):
    old_source = read_old_source()
    with tempfile.TemporaryDirectory(prefix='abs-v020-compat-') as directory:
        root = Path(directory)
        module_path = root / 'old_module.py'
        module_path.write_bytes(old_source)
        spec = importlib.util.spec_from_file_location('_abs_real_v020', module_path)
        old = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = old
        try:
            spec.loader.exec_module(old)
            first = revised_pack(root / 'first.zip', b'# First documented fixture revision\nG14 unchanged\n')
            second = revised_pack(root / 'second.zip',
                                  '# Second fixture revision \u2013 G14 recoded\n'.encode('cp1252'))
            out = root / 'output'
            manifest_path = out / '2021_GCP_POA_NSW_manifest.json'
            def manifest():
                return json.loads(manifest_path.read_text())
            old.export_census(source_zip=first, tables=['G01', 'G02'], out=out)
            captured = manifest()
            require(captured['census_year'] == 2021 and captured['asgs_edition'] == 3,
                    'Real old writer identity differs')
            require('manifest_schema_version' not in captured, 'Old writer unexpectedly versions schema')
            current.export_census(source_zip=second, tables=['G04'], out=out)
            forward = manifest()
            require(forward['tables']['G01']['source_revision'] is None,
                    'Legacy table inherited latest-call revision')
            current.export_census(source_zip=first, tables=['G01', 'G02'], out=out)
            before_old = manifest()
            old.export_census(source_zip=second, tables=['G02'], out=out)
            mixed = manifest()
            require('manifest_schema_version' not in mixed, 'Old writer must drop new top-level schema')
            require('source_revision' not in mixed['tables']['G02'],
                    'Old writer must drop re-exported table revision')
            require(mixed['tables']['G01']['source_revision'] == before_old['tables']['G01']['source_revision'],
                    'Old writer lost retained optional provenance')
            current.export_census(source_zip=second, tables=['G04'], out=out)
            final = manifest()
            require(final['manifest_schema_version'] == 1 and final['asgs_edition'] == 3,
                    'New reader rejected legacy schema or changed old identity')
            retained = final['tables']['G01']['source_revision']
            previous_reference = before_old['tables']['G01']['source_revision']['documents'][0]
            require(retained['documents'][0]['sha256'] == previous_reference['sha256'],
                    'Retained revision hash overwritten after mixed writer round trip')
            require(retained['documents'][0]['text_status'] == 'missing'
                    and retained['status'] == 'unavailable',
                    'Old-writer document loss was not made explicit')
            require(final['tables']['G02']['source_revision'] is None,
                    'Missing revision inherited latest summary')
            for field, expected in {'census_year': 2021, 'profile': 'GCP', 'data_years': [2021],
                                    'boundary': current._boundary_details('POA', 2021)}.items():
                require(final['tables']['G02'][field] == expected,
                        'Validated legacy identity not derived: ' + field)
            require('writer_version' not in final['tables']['G02'], 'Invented legacy writer version')
            require('source_documents' not in mixed, 'Old writer must drop new document store')
            digest = final['tables']['G01']['source_revision']['documents'][0]['sha256']
            require(digest not in final['source_documents'],
                    'Lost historical document text must not be replaced by latest revision')
            require(final['source_revision'] != final['tables']['G01']['source_revision'],
                    'Different revisions no longer distinguished')
            if fixture:
                require(not fixture.exists(), 'Refusing to overwrite fixture: ' + str(fixture))
                # Normalize disposable source roots only, not actual old-writer fields.
                normalized = json.loads(json.dumps(captured).replace(str(root).replace('\\', '\\\\'),
                                                                     '__FIXTURE_ROOT__'))
                payload = {'generator': 'tests/check_compat.py --write-fixture PATH',
                           'old_tag': 'v0.2.0', 'old_module_sha256': hashlib.sha256(old_source).hexdigest(),
                           'normalization': 'Disposable absolute source root replaced by __FIXTURE_ROOT__ only',
                           'manifest': normalized}
                fixture.parent.mkdir(parents=True, exist_ok=True)
                fixture.write_text(json.dumps(payload, indent=2) + '\n', encoding='utf-8')
            result = {'old_tag': 'v0.2.0', 'old_module_sha256': hashlib.sha256(old_source).hexdigest(),
                      'runtime_sha256': hashlib.sha256((ROOT/'abs_census_etl.py').read_bytes()).hexdigest(),
                      'network_used': False, 'old_to_new': True, 'new_to_old_to_new': True,
                      'retained_revision_hash_survives': True, 'dropped_revision_stays_unknown': True}
            if report:
                report.parent.mkdir(parents=True, exist_ok=True)
                report.write_text(json.dumps(result, indent=2)+'\n', encoding='utf-8')
        finally:
            sys.modules.pop(spec.name, None)
    print('PASS: actual v0.2.0 → new and new → v0.2.0 → new manifest round trips')
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path)
    parser.add_argument('--write-fixture', type=Path,
                        help='Capture an actual old manifest; existing destinations are refused')
    args = parser.parse_args(argv)
    try:
        run_check(report=args.report, fixture=args.write_fixture)
    except (OSError, RuntimeError, ValueError) as error:
        print('ERROR: ' + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
