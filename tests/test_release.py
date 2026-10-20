"""Offline release integrity and publication-gate regressions; no API writes."""
import hashlib
import importlib.util
import io
import os
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import subprocess
from urllib.error import HTTPError
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('release_tools', ROOT / 'scripts/release.py')
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)
TAG = 'v1.2.3'
COMMIT = 'a' * 40
LICENSE = (ROOT / 'LICENSE').read_bytes()


def sums(folder, tag=TAG):
    (folder / 'SHA256SUMS').write_bytes(''.join(
        f'{release.digest(folder / n)}  {n}\n' for n in sorted(release.asset_names(tag[1:]))).encode('ascii'))


def bundle(folder, *, tag=TAG, wheel_version=None, mode=0o644,
           wheel_license=LICENSE, sdist_license=LICENSE):
    version = tag[1:]
    for name in ('install.sh', 'install.ps1'):
        (folder / name).write_text('installer')
    info = f'abs_census_etl-{version}.dist-info'
    with ZipFile(folder / f'abs_census_etl-{version}-py3-none-any.whl', 'w') as z:
        z.writestr('abs_census_etl.py', 'module')
        for name in ('METADATA', 'WHEEL', 'RECORD', 'entry_points.txt', 'top_level.txt'):
            data = (f'Name: abs-census-etl\nVersion: {wheel_version or version}\n'
                    if name == 'METADATA' else '')
            z.writestr(info + '/' + name, data)
        z.writestr(info + '/licenses/LICENSE', wheel_license)
    with tarfile.open(folder / f'abs_census_etl-{version}.tar.gz', 'w:gz') as t:
        for name in sorted(release.helpers()['expected_sdist'](version)):
            data = b'module' if name.endswith('/abs_census_etl.py') else b'placeholder'
            if name.endswith('/pyproject.toml'):
                data = f'[project]\nversion="{version}"\n'.encode()
            if name.endswith('/uv.lock'):
                data = f'[[package]]\nname="abs-census-etl"\nversion="{version}"\n'.encode()
            if name.endswith('/LICENSE'):
                data = sdist_license
            entry = tarfile.TarInfo(name); entry.size = len(data); entry.mode = mode
            t.addfile(entry, io.BytesIO(data))
    sums(folder, tag)


def git_environment(base):
    """Run Git without the caller's global/system config, hooks, signing or GIT_* state."""
    config = base / 'empty-gitconfig'; config.write_text('')
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith('GIT_')}
    identity = {'GIT_AUTHOR_NAME': 'Offline test', 'GIT_COMMITTER_NAME': 'Offline test',
                'GIT_AUTHOR_EMAIL': 'offline@example.invalid',
                'GIT_COMMITTER_EMAIL': 'offline@example.invalid'}
    return dict(env, GIT_CONFIG_GLOBAL=str(config), GIT_CONFIG_NOSYSTEM='1', **identity)


class FakeAPI:
    def __init__(self, *, existing=False, moved=False, bad_asset=False,
                 draft=False, late_move=False):
        self.existing = existing; self.moved = moved; self.bad_asset = bad_asset
        self.draft = draft; self.late_move = late_move; self.ref_calls = 0
        self.calls = []; self.assets = []

    def request(self, method, path, data=None, *, binary=False):
        self.calls.append((method, path, data))
        if path.startswith('/git/ref/'):
            self.ref_calls += 1
            if self.late_move and self.ref_calls > 1:
                return {'object': {'type': 'commit', 'sha': 'b' * 40}}
            return {'object': {'type': 'commit', 'sha': 'b' * 40 if self.moved else COMMIT}}
        if path.startswith('/releases/tags/'):
            if self.existing:
                return {'id': 1}
            raise HTTPError(path, 404, 'not found', {}, None)
        if path.startswith('/releases?'):
            return [{'tag_name': TAG}] if self.draft else []
        if path == '/releases' and method == 'POST':
            return {'id': 12, 'upload_url':
                    'https://uploads.github.com/repos/o/r/releases/12/assets{?name}'}
        if path.startswith('https://uploads.github.com/'):
            from urllib.parse import parse_qs, urlsplit
            name = parse_qs(urlsplit(path).query)['name'][0]
            value = {'name': name, 'state': 'uploaded', 'digest': 'sha256:' + hashlib.sha256(data).hexdigest()}
            if self.bad_asset:
                value['name'] = 'renamed'
            self.assets.append(value)
            return {'name': value['name'], 'digest': None}
        if path.endswith('/assets?per_page=100'):
            return self.assets
        if method == 'PATCH':
            return {'draft': False}
        raise AssertionError((method, path))


class ReleaseTests(unittest.TestCase):
    def test_public_canonical_version_only(self):
        for tag in ('v1.0', 'v1.2.3rc1.post2.dev3', 'v0.1.0.dev0'):
            self.assertEqual(release.version_from_tag(tag), tag[1:])
        for tag in ('1.0', 'v01.0', 'v1.0-rc1', 'v1.0+local', 'v1!1.0',
                    'v1.0\n', 'v1.0;echo bad', 'v1.0RC1'):
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                release.version_from_tag(tag)

    def test_bundle_requires_exact_assets_and_checksums(self):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td); bundle(folder)
            self.assertEqual(set(release.verify(folder, TAG)),
                             release.asset_names(TAG[1:]) | {'SHA256SUMS'})
            original = (folder / 'SHA256SUMS').read_text()
            for text in (original + original.splitlines()[0] + '\n', original.upper(), '',
                         original.replace('  install.sh', '  ../install.sh'),
                         original.replace('\n', '\r\n'), original.replace('\n', '\v'),
                         original.replace('\n', '\f'), original.rstrip('\n')):
                with self.subTest(text=text[:20]), self.assertRaises(ValueError):
                    (folder / 'SHA256SUMS').write_bytes(text.encode('ascii')); release.verify(folder, TAG)
            (folder / 'SHA256SUMS').write_bytes(original.encode('ascii'))
            (folder / 'install.sh').write_text('tampered')
            with self.assertRaisesRegex(ValueError, 'Checksum mismatch'):
                release.verify(folder, TAG)
            sums(folder); (folder / 'surprise').write_text('extra')
            with self.assertRaisesRegex(ValueError, 'exactly'):
                release.verify(folder, TAG)

    def test_wheel_version_and_archive_permissions_checked(self):
        for kwargs, message in (({'wheel_version': '9.0'}, 'Wheel metadata'),
                                ({'mode': 0o600}, 'readable')):
            with tempfile.TemporaryDirectory() as td, self.subTest(kwargs=kwargs):
                folder = Path(td); bundle(folder, **kwargs)
                with self.assertRaisesRegex(ValueError, message):
                    release.verify(folder, TAG)

    def test_nonempty_output_refused_before_build(self):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td); marker = folder / 'keep'; marker.write_text('untouched')
            import tomllib
            tag = 'v' + tomllib.loads((ROOT / 'pyproject.toml').read_text())['project']['version']
            with self.assertRaisesRegex(ValueError, 'nonempty'):
                release.build(folder, tag)
            self.assertEqual(marker.read_text(), 'untouched')

    def test_confirmation_existing_release_and_moved_tag_refuse_writes(self):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td); bundle(folder)
            for kwargs, confirm, message in (({}, False, 'confirm'),
                    ({'existing': True}, True, 'already exists'),
                    ({'draft': True}, True, 'already exists'),
                    ({'moved': True}, True, 'Tag moved')):
                api = FakeAPI(**kwargs)
                with self.subTest(kwargs=kwargs), self.assertRaisesRegex(ValueError, message):
                    release.publish(api, folder, TAG, COMMIT, confirm=confirm)
                self.assertFalse(any(method != 'GET' for method, _, _ in api.calls))

    def test_publish_uploads_draft_then_verifies_before_visibility(self):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td); bundle(folder); api = FakeAPI()
            result = release.publish(api, folder, TAG, COMMIT, confirm=True)
            self.assertFalse(result['draft'])
            create = next(data for method, path, data in api.calls
                          if method == 'POST' and path == '/releases')
            self.assertTrue(create['draft'])
            self.assertFalse(create['prerelease'])
            self.assertEqual(len(api.assets), 5)
            self.assertEqual(api.calls[-1], ('PATCH', '/releases/12', {'draft': False, 'make_latest': 'true'}))
            api = FakeAPI(bad_asset=True)
            with self.assertRaisesRegex(ValueError, 'uploaded name'):
                release.publish(api, folder, TAG, COMMIT, confirm=True)
            self.assertFalse(any(method == 'PATCH' for method, _, _ in api.calls))
            api = FakeAPI(late_move=True)
            with self.assertRaisesRegex(ValueError, 'Tag moved during upload'):
                release.publish(api, folder, TAG, COMMIT, confirm=True)
            self.assertFalse(any(method == 'PATCH' for method, _, _ in api.calls))

    def test_invalid_bundle_makes_zero_api_calls(self):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td); bundle(folder); api = FakeAPI()
            (folder / 'install.sh').write_bytes(b'tampered')
            with self.assertRaisesRegex(ValueError, 'Checksum mismatch'):
                release.publish(api, folder, TAG, COMMIT, confirm=True)
            self.assertEqual(api.calls, [])
            (folder / 'install.sh').write_bytes(b'line\r\n'); sums(folder)
            with self.assertRaisesRegex(ValueError, 'Installer must use LF'):
                release.verify(folder, TAG)

    def test_latest_policy_never_promotes_old_patch_or_prerelease(self):
        published = [{'tag_name': 'v1.0.0'}]
        for tag in ('v0.9.9', 'v1.0', 'v1.0.0rc1', 'v2.0.dev0'):
            self.assertEqual(release.latest_policy(tag, published), 'false')
        for tag in ('v1.0.1', 'v1.0.post1', 'v2.0'):
            self.assertEqual(release.latest_policy(tag, published), 'true')
        self.assertEqual(release.latest_policy('v1.0', []), 'true')

    def test_resolve_lightweight_annotated_and_nonancestor_tags(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / 'repo'; root.mkdir(); env = git_environment(Path(td))
            def git(*args):
                return subprocess.check_output(['git', *args], cwd=root, env=env, text=True).strip()
            git('init', '-q', '-b', 'main')
            git('commit', '--allow-empty', '-qm', 'first')
            first = git('rev-parse', 'HEAD'); git('tag', 'v1.0')
            git('tag', '-a', 'v1.1', '-m', 'annotated')
            with patch.dict(os.environ, env, clear=True):
                self.assertEqual(release.resolve_tag('v1.0', root), first)
                self.assertEqual(release.resolve_tag('v1.1', root), first)
            git('checkout', '-q', '--orphan', 'other')
            git('commit', '--allow-empty', '-qm', 'unrelated')
            with patch.dict(os.environ, env, clear=True), \
                    self.assertRaises(subprocess.CalledProcessError):
                release.resolve_tag('v1.0', root)

    def test_api_annotation_non404_and_upload_set_errors(self):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td); bundle(folder)
            api = FakeAPI(); original = api.request
            def annotated(method, path, data=None, **kwargs):
                if path.startswith('/git/ref/'):
                    return {'object': {'type': 'tag', 'sha': 'c' * 40}}
                if path.startswith('/git/tags/'):
                    return {'object': {'type': 'commit', 'sha': COMMIT}}
                return original(method, path, data, **kwargs)
            with patch.object(api, 'request', side_effect=annotated):
                self.assertEqual(release.tag_commit(api, TAG), COMMIT)
            for code in (403, 500):
                api = FakeAPI(); original = api.request
                def fail(method, path, data=None, **kwargs):
                    if path.startswith('/releases/tags/'):
                        raise HTTPError(path, code, 'server detail', {}, None)
                    return original(method, path, data, **kwargs)
                with patch.object(api, 'request', side_effect=fail), self.assertRaises(HTTPError) as caught:
                    release.publish(api, folder, TAG, COMMIT, confirm=True)
                caught.exception.close()
                self.assertFalse(any(m != 'GET' for m, _, _ in api.calls))
            api = FakeAPI(); original = api.request
            def extra(method, path, data=None, **kwargs):
                result = original(method, path, data, **kwargs)
                return result + [{'name': 'extra'}] if path.endswith('/assets?per_page=100') else result
            with patch.object(api, 'request', side_effect=extra), self.assertRaisesRegex(
                    ValueError, 'asset set differs'):
                release.publish(api, folder, TAG, COMMIT, confirm=True)
            self.assertFalse(any(m == 'PATCH' for m, _, _ in api.calls))

    def test_asset_digest_poll_waits_for_listing(self):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td); bundle(folder); api = FakeAPI(); original = api.request
            polls = []
            def delayed(method, path, data=None, **kwargs):
                result = original(method, path, data, **kwargs)
                if path.endswith('/assets?per_page=100'):
                    polls.append(1)
                    if len(polls) == 1:
                        return [dict(a, digest=None) for a in result]
                return result
            with patch.object(api, 'request', side_effect=delayed), patch.object(release.time, 'sleep'):
                release.publish(api, folder, TAG, COMMIT, confirm=True)
            self.assertEqual(len(polls), 2)

    def test_prerelease_publishes_as_prerelease_without_latest(self):
        tag = 'v1.2.3rc1'
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td); bundle(folder, tag=tag); api = FakeAPI()
            release.publish(api, folder, tag, COMMIT, confirm=True)
            create = next(d for m, p, d in api.calls if m == 'POST' and p == '/releases')
            self.assertTrue(create['draft'])
            self.assertTrue(create['prerelease'])
            self.assertEqual(api.calls[-1], ('PATCH', '/releases/12',
                                             {'draft': False, 'make_latest': 'false'}))

    def test_license_must_match_trusted_checkout(self):
        self.assertIn(b'MIT License', LICENSE)
        changed = LICENSE.replace(b'Copyright', b'Copyleft')
        for kwargs, message in (({'sdist_license': changed}, 'Source archive LICENSE'),
                                ({'wheel_license': changed}, 'Wheel LICENSE')):
            with tempfile.TemporaryDirectory() as td, self.subTest(kwargs=sorted(kwargs)):
                folder = Path(td); bundle(folder, **kwargs); api = FakeAPI()
                with self.assertRaisesRegex(ValueError, message):
                    release.publish(api, folder, TAG, COMMIT, confirm=True)
                self.assertEqual(api.calls, [])

    def test_newer_release_during_upload_prevents_latest_promotion(self):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td); bundle(folder); api = FakeAPI(); original = api.request
            def newer(method, path, data=None, **kwargs):
                result = original(method, path, data, **kwargs)
                if path.startswith('/releases?') and api.assets:
                    return [{'tag_name': 'v2.0', 'draft': False, 'prerelease': False}]
                return result
            with patch.object(api, 'request', side_effect=newer):
                release.publish(api, folder, TAG, COMMIT, confirm=True)
            self.assertEqual(api.calls[-1][2]['make_latest'], 'false')

    def test_missing_or_wrong_listed_digest_never_publishes(self):
        for value in (None, 'sha256:' + '0' * 64):
            with tempfile.TemporaryDirectory() as td:
                folder = Path(td); bundle(folder); api = FakeAPI(); original = api.request
                def invalid(method, path, data=None, **kwargs):
                    result = original(method, path, data, **kwargs)
                    if path.endswith('/assets?per_page=100'):
                        return [dict(a, digest=value) for a in result]
                    return result
                with patch.object(api, 'request', side_effect=invalid), \
                        patch.object(release.time, 'sleep') as sleep, self.assertRaisesRegex(
                            ValueError, 'digests not ready|digest differs'):
                    release.publish(api, folder, TAG, COMMIT, confirm=True)
                self.assertEqual(sleep.call_count, 4 if value is None else 0)
                self.assertFalse(any(m == 'PATCH' for m, _, _ in api.calls))

    def test_autocrlf_checkout_keeps_installer_bytes(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / 'source'; source.mkdir()
            for name in ('.gitattributes', 'install.sh', 'install.ps1'):
                (source / name).write_bytes((ROOT / name).read_bytes())
            env = git_environment(Path(td))
            def git(*args, cwd=source):
                return subprocess.run(['git', *args], cwd=cwd, env=env, check=True,
                                      capture_output=True, text=True)
            git('init', '-q', '-b', 'main')
            git('add', '.'); git('commit', '-qm', 'fixtures')
            target = Path(td) / 'checkout'
            git('-c', 'core.autocrlf=true', 'clone', '-q', str(source), str(target))
            for name in ('install.sh', 'install.ps1'):
                self.assertEqual((target / name).read_bytes(), (ROOT / name).read_bytes())

    def test_cli_malformed_zip_is_clean_error_and_http_body_retained(self):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td); bundle(folder)
            wheel = next(folder.glob('*.whl')); wheel.write_bytes(b'not a zip'); sums(folder)
            import sys
            result = subprocess.run([sys.executable, '-I', str(ROOT / 'scripts/release.py'),
                'verify', '--tag', TAG, '--assets', td], capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            self.assertIn('ERROR:', result.stderr)
            self.assertNotIn('Traceback', result.stderr)
        error = HTTPError('https://api.github.com', 403, 'Forbidden', {},
                          io.BytesIO(b'{"message":"permission denied"}'))
        with patch.object(release.urllib.request, 'urlopen', side_effect=error):
            with self.assertRaises(HTTPError) as caught:
                release.GitHub('owner/repo', 'fake').request('GET', '/releases')
        self.assertIn('permission denied', str(caught.exception))
        error.close()


if __name__ == '__main__':
    unittest.main()
