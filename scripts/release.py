"""Build/verify GitHub bundles, or explicitly publish a new immutable-tag release.

No network writes occur except with the publish subcommand and --confirm.
"""
from __future__ import annotations

import argparse
import email.parser
import hashlib
import json
import os
from pathlib import Path
import re
import runpy
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from zipfile import BadZipFile, ZipFile

ROOT = Path(__file__).resolve().parents[1]
VERSION = (r"(0|[1-9][0-9]*)(\.(0|[1-9][0-9]*))*((a|b|rc)(0|[1-9][0-9]*))?"
           r"(\.post(0|[1-9][0-9]*))?(\.dev(0|[1-9][0-9]*))?")


def require(ok, message):
    if not ok:
        raise ValueError(message)


def version_from_tag(tag):
    require(re.fullmatch('v' + VERSION, tag, flags=re.ASCII), 'Use a canonical public vVERSION tag')
    return tag[1:]


def asset_names(version):
    return {f'abs_census_etl-{version}.tar.gz',
            f'abs_census_etl-{version}-py3-none-any.whl', 'install.sh', 'install.ps1'}


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def helpers():
    return runpy.run_path(str(ROOT / 'tests/check_packaging.py'))


def verify(folder, tag):
    version = version_from_tag(tag)
    expected = asset_names(version)
    require(folder.is_dir() and not folder.is_symlink(), 'Expected a regular asset directory')
    require({p.name for p in folder.iterdir()} == expected | {'SHA256SUMS'},
            'Release directory must contain exactly the five contracted assets')
    require(all(p.is_file() and not p.is_symlink() for p in folder.iterdir()),
            'Release assets must be regular files, without links')
    entries = {}
    raw = (folder / 'SHA256SUMS').read_bytes()
    require(raw.endswith(b'\n') and not any(c in raw for c in (b'\r', b'\v', b'\f')),
            'SHA256SUMS must use LF lines and a final newline')
    for name in ('install.sh', 'install.ps1'):
        require(b'\r' not in (folder / name).read_bytes(), 'Installer must use LF: ' + name)
    for line in raw.decode('ascii').split('\n')[:-1]:
        match = re.fullmatch(r'([0-9a-f]{64})  ([A-Za-z0-9_.-]+)', line)
        require(match is not None, 'Invalid SHA256SUMS entry')
        value, name = match.groups()
        require(name not in entries, 'Duplicate SHA256SUMS entry')
        entries[name] = value
    require(set(entries) == expected, 'Missing or unexpected checksum entries')
    for name, value in entries.items():
        require(digest(folder / name) == value, f'Checksum mismatch: {name}')
    # The trusted checkout's license must ship unchanged in both archives.
    license_text = (ROOT / 'LICENSE').read_bytes()
    require(license_text.strip(), 'Trusted checkout LICENSE is empty')
    wheel, sdist = helpers()['check_artifacts'](folder, version)
    with ZipFile(wheel) as z:
        info = f'abs_census_etl-{version}.dist-info/'
        metadata = email.parser.BytesParser().parsebytes(z.read(info + 'METADATA'))
        module = z.read('abs_census_etl.py')
        require(z.read(info + 'licenses/LICENSE') == license_text,
                'Wheel LICENSE differs from the trusted checkout')
    require(metadata['Name'] == 'abs-census-etl' and metadata['Version'] == version,
            'Wheel metadata/tag mismatch')
    with tarfile.open(sdist) as t:
        prefix = f'abs_census_etl-{version}/'
        project = tomllib.loads(t.extractfile(prefix + 'pyproject.toml').read().decode())
        lock = tomllib.loads(t.extractfile(prefix + 'uv.lock').read().decode())
        require(t.extractfile(prefix + 'abs_census_etl.py').read() == module,
                'Wheel/source module mismatch')
        require(t.extractfile(prefix + 'LICENSE').read() == license_text,
                'Source archive LICENSE differs from the trusted checkout')
        require(all(m.mode & 0o044 == 0o044 for m in t.getmembers() if m.isfile()),
                'Source archive files must be readable by other users')
    require(project['project']['version'] == version, 'Source metadata/tag mismatch')
    app = [p for p in lock['package'] if p['name'] == 'abs-census-etl']
    require(len(app) == 1 and app[0]['version'] == version, 'Lock/source version mismatch')
    return {name: digest(folder / name) for name in sorted(expected | {'SHA256SUMS'})}


def build(folder, tag):
    version = version_from_tag(tag)
    require(tomllib.loads((ROOT / 'pyproject.toml').read_text())['project']['version'] == version,
            'Tag must equal project.version')
    require(not folder.exists() or (folder.is_dir() and not any(folder.iterdir())),
            'Refusing a nonempty output directory; use a fresh folder')
    require(not folder.is_symlink(), 'Refusing a linked output directory')
    uv = shutil.which('uv')
    require(uv, 'Release building requires uv on PATH')
    h = helpers()
    with tempfile.TemporaryDirectory(prefix='abs-release-') as td:
        scratch = h['guarded_workspace'](Path(td))
        source = scratch / 'source'
        h['copy_build_source'](ROOT, source)
        for path in source.rglob('*'):
            path.chmod(0o755 if path.is_dir() else 0o644)
        env = h['uv_environment'](h['isolated_environment'](dict(os.environ)), scratch / 'config')
        subprocess.run([uv, 'lock', '--check', '--python', sys.executable],
                       cwd=source, env=env, check=True, timeout=180)
        output = scratch / 'assets'
        subprocess.run([uv, 'build', '--python', sys.executable, '--out-dir', str(output),
                        '--default-index', 'https://pypi.org/simple', '--build-constraints',
                        str(ROOT / 'release-build-constraints.txt'), '--require-hashes'],
                       cwd=source, env=env, check=True, timeout=300,
                       **({'umask': 0o022} if os.name == 'posix' else {}))
        # uv writes its own .gitignore in build output. It is not a release asset.
        built = {p.name for p in output.iterdir()}
        application = asset_names(version) - {'install.sh', 'install.ps1'}
        require(built in (application, application | {'.gitignore'}),
                'Unexpected build output: ' + repr(sorted(built)))
        (output / '.gitignore').unlink(missing_ok=True)
        for name in ('install.sh', 'install.ps1'):
            require(not (ROOT / name).is_symlink(), f'Linked installer: {name}')
            shutil.copyfile(ROOT / name, output / name)
            (output / name).chmod(0o644)
        sums = ''.join(f'{digest(output / name)}  {name}\n'
                       for name in sorted(asset_names(version)))
        (output / 'SHA256SUMS').write_bytes(sums.encode('ascii'))
        result = verify(output, tag)
        folder.mkdir(parents=True, exist_ok=True)
        for path in output.iterdir():
            # Exclusive creation prevents silently replacing a concurrent build.
            with path.open('rb') as src, (folder / path.name).open('xb') as dst:
                shutil.copyfileobj(src, dst)
            (folder / path.name).chmod(0o644)
    return result


class GitHub:
    def __init__(self, repository, token):
        require(re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository), 'Invalid repository')
        self.base = 'https://api.github.com/repos/' + repository
        self.token = token

    def request(self, method, path, data=None, *, binary=False):
        url = path if path.startswith('https://uploads.github.com/') else self.base + path
        require(url.startswith((self.base + '/', 'https://uploads.github.com/')),
                'Refusing unexpected API host')
        body = data if binary else (json.dumps(data).encode() if data is not None else None)
        request = urllib.request.Request(url, data=body, method=method, headers={
            'Authorization': 'Bearer ' + self.token, 'Accept': 'application/vnd.github+json',
            'X-GitHub-Api-Version': '2022-11-28',
            'Content-Type': 'application/octet-stream' if binary else 'application/json',
        })
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            # Preserve the status (404 is handled by callers) and server diagnostics.
            detail = error.read(8192).decode('utf-8', errors='replace')
            error.msg = str(error.reason) + ': ' + detail
            raise


def tag_commit(api, tag):
    value = api.request('GET', '/git/ref/tags/' + urllib.parse.quote(tag, safe=''))['object']
    for _ in range(10):
        if value['type'] == 'commit':
            return value['sha']
        require(value['type'] == 'tag', 'Tag does not refer to a commit')
        value = api.request('GET', '/git/tags/' + value['sha'])['object']
    raise ValueError('Tag nesting exceeds limit')


def publish(api, folder, tag, commit, *, confirm=False):
    require(confirm, 'Publication requires --confirm')
    require(isinstance(commit, str) and re.fullmatch(r'[0-9a-f]{40}', commit),
            'Expected an exact commit SHA')
    hashes = verify(folder, tag)
    require(tag_commit(api, tag) == commit, 'Tag moved or differs from tested commit')
    try:
        api.request('GET', '/releases/tags/' + urllib.parse.quote(tag, safe=''))
    except urllib.error.HTTPError as error:
        if error.code != 404:
            raise
        error.close()
    else:
        raise ValueError('Release already exists; assets are immutable. Use a new version/tag.')
    # The tag endpoint excludes drafts. List releases to find authenticated drafts.
    releases = list_releases(api)
    require(not any(r['tag_name'] == tag for r in releases), 'Release/draft already exists')
    # Validate existing stable tags before any write; the decision is made again later.
    latest_policy(tag, stable_releases(releases))
    release = api.request('POST', '/releases', {'tag_name': tag, 'target_commitish': commit,
                          'name': tag, 'draft': True, 'prerelease': bool(
                              re.search(r'(a|b|rc|\.dev)[0-9]', tag))})
    url = release['upload_url'].split('{', 1)[0]
    for name, value in hashes.items():
        uploaded = api.request('POST', url + '?name=' + urllib.parse.quote(name, safe=''),
                               (folder / name).read_bytes(), binary=True)
        require(uploaded['name'] == name,
                'GitHub changed an uploaded name; draft retained for inspection')
    expected = {n: 'sha256:' + h for n, h in hashes.items()}
    for attempt in range(5):
        assets = api.request('GET', f"/releases/{release['id']}/assets?per_page=100")
        require(len(assets) == len(hashes) and {a['name'] for a in assets} == set(hashes),
                'Uploaded asset set differs; draft retained')
        actual = {a['name']: a.get('digest') for a in assets}
        require(all(value is None or value == expected[name] for name, value in actual.items()),
                'Uploaded asset digest differs; draft retained')
        if actual == expected and all(a.get('state') == 'uploaded' for a in assets):
            break
        require(attempt < 4, 'Uploaded asset digests not ready; draft retained')
        time.sleep(2)
    # Re-evaluate after uploads; another release may have appeared meanwhile.
    latest = latest_policy(tag, stable_releases(list_releases(api)))
    require(tag_commit(api, tag) == commit, 'Tag moved during upload; draft retained')
    return api.request('PATCH', f"/releases/{release['id']}",
                       {'draft': False, 'make_latest': latest})


def list_releases(api):
    releases, page = [], 1
    while True:
        batch = api.request('GET', f'/releases?per_page=100&page={page}')
        releases.extend(batch)
        if len(batch) < 100:
            return releases
        page += 1


def stable_releases(releases):
    return [r for r in releases if not r.get('draft') and not r.get('prerelease')]


def stable_key(tag):
    version = version_from_tag(tag)
    require(not re.search(r'(a|b|rc|\.dev)[0-9]', version),
            'Published stable release has a prerelease version')
    base, _, post = version.partition('.post')
    parts = [int(p) for p in base.split('.')]
    while len(parts) > 1 and parts[-1] == 0:
        parts.pop()
    return tuple(parts), int(post) if post else -1


def latest_policy(tag, published):
    if re.search(r'(a|b|rc|\.dev)[0-9]', tag):
        return 'false'
    key = stable_key(tag)
    return 'true' if all(key > stable_key(r['tag_name']) for r in published) else 'false'


def resolve_tag(tag, root=ROOT):
    version_from_tag(tag)
    sha = subprocess.check_output(['git', 'rev-parse', '--verify',
                                   'refs/tags/' + tag + '^{commit}'],
                                  cwd=root, text=True).strip()
    subprocess.run(['git', 'merge-base', '--is-ancestor', sha, 'HEAD'], cwd=root, check=True)
    require(re.fullmatch('[0-9a-f]{40}', sha), 'Invalid resolved commit')
    return sha


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('build', 'verify', 'resolve-tag', 'publish'))
    parser.add_argument('--tag', required=True)
    parser.add_argument('--assets', type=Path, default=ROOT / 'dist/release')
    parser.add_argument('--commit')
    parser.add_argument('--confirm', action='store_true')
    args = parser.parse_args()
    if args.command == 'resolve-tag':
        sha = resolve_tag(args.tag)
        print(sha)
        if os.environ.get('GITHUB_OUTPUT'):
            with open(os.environ['GITHUB_OUTPUT'], 'a') as stream:
                stream.write('commit=' + sha + '\n')
        return
    if args.command == 'publish':
        require(args.confirm, 'Publication requires --confirm')
        token = os.environ.get('GITHUB_TOKEN')
        require(token, 'GITHUB_TOKEN is required for explicit publication')
        api = GitHub(os.environ['GITHUB_REPOSITORY'], token)
        result = publish(api, args.assets, args.tag, args.commit, confirm=True)
    else:
        result = (build if args.command == 'build' else verify)(args.assets, args.tag)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.SubprocessError, KeyError, RuntimeError,
            BadZipFile, tarfile.TarError) as error:
        print(f'ERROR: {error}', file=sys.stderr)
        raise SystemExit(1)
