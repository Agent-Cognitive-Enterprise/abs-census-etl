"""Offline integration tests for the real shell installer in private homes.

The local trusted uv double exercises lifecycle decisions, not package-manager
correctness; tests/check_installer.py exercises the actual bootstrap/toolchain.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
FAKE_UV = r'''
import json, os, pathlib, sys, time, tomllib
args = sys.argv[1:]
log = pathlib.Path(os.environ['FAKE_UV_LOG'])
with log.open('a') as stream:
    stream.write(json.dumps({'args':args,'cwd':str(pathlib.Path.cwd()),
                            'python_root':os.environ.get('UV_PYTHON_INSTALL_DIR'),
                            'override':os.environ.get('UV_OVERRIDE'),
                            'env':dict(os.environ)})+'\n')
if args == ['--version']:
    print('uv '+os.environ.get('FAKE_UV_VERSION','0.12.21')+' (test-build)'); sys.exit(0)
if args[:2] == ['python','install']:
    sys.exit(0)
if args[:2] == ['python','find']:
    print(os.environ['FAKE_REAL_PYTHON']); sys.exit(0)
if args[:2] == ['pip', 'install']:
    import zipfile, email
    with zipfile.ZipFile(args[-1]) as archive:
        info=email.message_from_bytes(archive.read('abs_census_etl.dist-info/METADATA'))
    path=pathlib.Path('.venv/bin/python')
    text=path.read_text()
    import re
    version=os.environ.get('FAKE_INSTALLED_VERSION', info['Version'])
    text=re.sub(r'^version=.*$', 'version='+repr(version), text, flags=re.MULTILINE)
    path.write_text(text)
    sys.exit(0)
if args[0] != 'sync':
    raise SystemExit('Unexpected uv invocation')
for required in ('--locked','--no-install-project','--no-dev','--managed-python','--no-build'):
    if required not in args: raise SystemExit('Missing installer constraint: '+required)
version=tomllib.loads(pathlib.Path('pyproject.toml').read_text())['project']['version']
if version == os.environ.get('FAKE_FAIL_SYNC'):
    raise SystemExit('simulated sync failure')
if os.environ.get('FAKE_WAIT'):
    pathlib.Path(os.environ['FAKE_WAIT']).write_text('ready')
    time.sleep(30)
folder=pathlib.Path('.venv/bin')
folder.mkdir(parents=True)
failure=version == os.environ.get('FAKE_FAIL_SMOKE')
payload='#!'+os.environ['FAKE_REAL_PYTHON']+'\n'
payload+='import sys\n'
payload+='version='+repr(version)+'\n'
payload+='if "--help" in sys.argv and '+repr(failure)+': raise SystemExit(9)\n'
payload+='if "-" in sys.argv:\n'
payload+='    import importlib.metadata, types\n'
payload+='    importlib.metadata.version=lambda name: version\n'
payload+='    sys.modules["abs_census_etl"]=types.ModuleType("abs_census_etl")\n'
payload+='    sys.modules["pyarrow"]=types.ModuleType("pyarrow")\n'
payload+='    sys.argv=sys.argv[sys.argv.index("-"):]\n'
payload+='    exec(compile(sys.stdin.read(), "<installer-check>", "exec"))\n'
payload+='    raise SystemExit(0)\n'
payload+='if "--echo-args" in sys.argv:\n'
payload+='    import json\n'
payload+='    print(json.dumps(sys.argv[sys.argv.index("--echo-args"):]))\n'
payload+='    raise SystemExit(0)\n'
payload+='print("FAKE_APP "+version)\n'
path=folder/'python'
path.write_text(payload)
path.chmod(0o700)
'''
FAKE_CURL = r'''
import json, os, pathlib, shutil, sys
args=sys.argv[1:]
url=args[-1]
with pathlib.Path(os.environ['FAKE_CURL_LOG']).open('a') as stream:
    stream.write(json.dumps(args)+'\n')
if os.environ.get('FAKE_FAIL_DOWNLOAD') and url.endswith(os.environ['FAKE_FAIL_DOWNLOAD']):
    raise SystemExit(22)
if url.endswith('/releases/latest'):
    default=url.removesuffix('/latest')+'/tag/v'
    print(os.environ.get('FAKE_LATEST_URL', default+os.environ['FAKE_LATEST']),end='')
    raise SystemExit(0)
output=pathlib.Path(args[args.index('--output' if '--output' in args else '-o')+1])
if '/astral-sh/uv/' in url:
    output.write_bytes(b'not the pinned uv archive')
else:
    shutil.copyfile(pathlib.Path(os.environ['FAKE_RELEASE'])/url.rsplit('/',1)[-1],output)
'''


@unittest.skipUnless(os.name == "posix" and getattr(os, "geteuid", lambda: 0)() != 0,
                     "Shell installer is user-local POSIX software")
class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="abs installer '")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.home = self.base / "home"
        self.home.mkdir()
        self.prefix = self.home / ".local/share/abs-census-etl"
        self.bin = self.home / ".local/bin"
        self.release = self.base / "release assets"
        self.release.mkdir()
        self.tools = self.base / "test tools"
        self.tools.mkdir()
        self.uv = self.tools / "uv-double"
        self.uv.write_text(f"#!{sys.executable}\n" + FAKE_UV)
        self.uv.chmod(0o700)
        self.log = self.base / "uv.log"
        self.env = dict(os.environ, HOME=str(self.home), FAKE_UV_LOG=str(self.log),
                        FAKE_REAL_PYTHON=sys.executable,
                        PATH=str(self.tools) + os.pathsep + os.environ["PATH"])
        self.version = "0.1.0.dev0"
        self.archive(self.version)

    def archive(self, version, *, member=None, kind=None, project_version=None):
        path = self.release / f"abs_census_etl-{version}.tar.gz"
        prefix = f"abs_census_etl-{version}"
        files = {name: (ROOT / name).read_bytes()
                 for name in ("abs_census_etl.py", "pyproject.toml", "uv.lock", "setup.cfg")}
        old = 'version = "0.1.0.dev0"'
        files["pyproject.toml"] = files["pyproject.toml"].replace(
            old.encode(), f'version = "{project_version or version}"'.encode(), 1)
        with tarfile.open(path, "w:gz") as archive:
            root = tarfile.TarInfo(prefix + "/")
            root.type = tarfile.DIRTYPE
            archive.addfile(root)
            for name, payload in files.items():
                entry = tarfile.TarInfo(f"{prefix}/{name}")
                entry.size = len(payload)
                archive.addfile(entry, io.BytesIO(payload))
            if member:
                entry = tarfile.TarInfo(member)
                if kind:
                    entry.type = kind
                    entry.linkname = "/tmp/not-an-installer-target"
                    archive.addfile(entry)
                else:
                    entry.size = 4
                    archive.addfile(entry, io.BytesIO(b"evil"))
        wheel = self.release / f"abs_census_etl-{version}-py3-none-any.whl"
        with zipfile.ZipFile(wheel, "w") as archive:
            archive.writestr("abs_census_etl.dist-info/METADATA",
                             f"Name: abs-census-etl\nVersion: {version}\n")
        self.checksums()
        return path

    def checksums(self):
        (self.release / "SHA256SUMS").write_text("".join(
            f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n"
            for p in sorted(self.release.iterdir()) if p.name != "SHA256SUMS"))

    def command(self, version=None, *extra):
        return ["sh", str(ROOT / "install.sh"), "--version", version or self.version,
                "--release-dir", str(self.release), "--uv", str(self.uv), *extra]

    def install(self, version=None, *extra, ok=True, env=None):
        result = subprocess.run(self.command(version, *extra), env=env or self.env,
                                cwd=self.base, capture_output=True, text=True, timeout=20)
        if ok:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def active(self):
        return (self.prefix / "active").read_text()

    def app(self):
        return subprocess.check_output([str(self.bin / "abs-census-etl"), "--help"],
                                       text=True, env=self.env, cwd=self.base).strip()

    def assert_clean(self):
        self.assertFalse((self.prefix / ".install-lock").exists())
        self.assertEqual(list(self.prefix.glob(".install.*")), [])
        self.assertEqual(list(self.bin.glob(".abs-census-etl.*")), [])

    def test_first_install_spaces_and_apostrophes_repeat_and_user_data(self):
        self.install()
        first = self.active()
        calls = self.log.read_text()
        self.assertEqual(self.prefix.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.app(), "FAKE_APP " + self.version)
        data = self.home / "abs_out/user.csv"
        data.parent.mkdir()
        data.write_text("user export")
        again = self.install()
        self.assertIn("Already installed", again.stdout)
        self.assertEqual(self.log.read_text(), calls)
        self.assertEqual(self.active(), first)
        self.assertEqual(data.read_text(), "user export")
        self.assert_clean()

    def test_update_retains_old_environment_and_data(self):
        self.install()
        old = self.active()
        original = self.prefix / "versions" / old.strip()
        (original / "keep").write_text("old version")
        self.archive("0.2.0")
        self.install("0.2.0")
        self.assertNotEqual(self.active(), old)
        self.assertEqual((original / "keep").read_text(), "old version")
        self.assertEqual(self.app(), "FAKE_APP 0.2.0")
        self.assert_clean()

    def test_failures_preserve_active_installation(self):
        self.install()
        old = self.active()
        for setting in ("FAKE_FAIL_SYNC", "FAKE_FAIL_SMOKE"):
            with self.subTest(setting=setting):
                self.archive("0.2.0")
                self.install("0.2.0", ok=False, env=dict(self.env, **{setting: "0.2.0"}))
                self.assertEqual(self.active(), old)
                self.assertEqual(self.app(), "FAKE_APP " + self.version)
                self.assertEqual(len(list((self.prefix / "versions").iterdir())), 1)
                self.assert_clean()

    def test_checksum_missing_duplicate_and_wrong_hash_fail_before_uv(self):
        archive = self.release / f"abs_census_etl-{self.version}.tar.gz"
        for contents in ("", f"{'0'*64}  {archive.name}\n", "bad  " + archive.name,
                         (self.release / "SHA256SUMS").read_text().strip() + " junk\n",
                         (self.release / "SHA256SUMS").read_text() * 2):
            with self.subTest(contents=contents):
                (self.release / "SHA256SUMS").write_text(contents)
                self.install(ok=False)
                self.assertFalse(self.log.exists())
                self.assertFalse((self.prefix / "active").exists())
                self.assert_clean()

    def test_archive_traversal_links_and_special_files_are_refused(self):
        for member, kind in (("../outside", None), ("/absolute", None),
                             (f"abs_census_etl-{self.version}/../outside", None),
                             (f"abs_census_etl-{self.version}/link", tarfile.SYMTYPE),
                             (f"abs_census_etl-{self.version}/hard", tarfile.LNKTYPE),
                             (f"abs_census_etl-{self.version}/fifo", tarfile.FIFOTYPE),
                             (f"abs_census_etl-{self.version}/plus+name.py", None),
                             (f"abs_census_etl-{self.version}/bang!name.py", None)):
            with self.subTest(member=member, kind=kind):
                self.archive(self.version, member=member, kind=kind)
                self.install(ok=False)
                self.assertFalse(self.log.exists())
                self.assert_clean()

    def test_project_version_mismatch_is_refused(self):
        self.archive(self.version, project_version="0.9.0")
        result = self.install(ok=False)
        self.assertIn("metadata disagree", result.stderr)
        self.assertFalse((self.prefix / "active").exists())
        self.assert_clean()

    def test_foreign_launcher_and_symlinked_state_are_preserved(self):
        self.bin.mkdir(parents=True)
        launcher = self.bin / "abs-census-etl"
        launcher.write_text("a foreign program")
        self.install(ok=False)
        self.assertEqual(launcher.read_text(), "a foreign program")
        self.assertFalse(self.log.exists())
        launcher.unlink()
        target = self.home / "unrelated"
        target.write_text("keep")
        (self.prefix / "active").symlink_to(target)
        self.install(ok=False)
        self.assertTrue((self.prefix / "active").is_symlink())
        self.assertEqual(target.read_text(), "keep")
        self.assert_clean()

    def test_foreign_install_root_is_refused(self):
        self.prefix.mkdir(parents=True)
        data = self.prefix / "not-ours"
        data.write_text("keep")
        self.install(ok=False)
        self.assertEqual(data.read_text(), "keep")
        self.assertFalse((self.prefix / ".abs-census-etl").exists())

    def test_external_uv_override_is_removed(self):
        hostile = {name: "/wrong" for name in (
            "UV_OVERRIDE", "UV_PROJECT_ENVIRONMENT", "UV_INDEX_URL", "UV_DEFAULT_INDEX",
            "PIP_INDEX_URL", "PIP_CONFIG_FILE", "PIP_CONSTRAINT", "VIRTUAL_ENV", "PYTHONPATH")}
        self.install(env=dict(self.env, **hostile))
        calls = [json.loads(line) for line in self.log.read_text().splitlines()]
        self.assertTrue(calls)
        for call in calls:
            for name in hostile:
                self.assertNotIn(name, call["env"], (name, call))
            self.assertEqual(call["python_root"], str((self.prefix / "python").resolve()))
            for name, suffix in (("XDG_CONFIG_HOME", "config/user"),
                                 ("XDG_CONFIG_DIRS", "config/system"),
                                 ("APPDATA", "config/user"), ("PROGRAMDATA", "config/system")):
                config = Path(call["env"][name])
                self.assertTrue(config.is_relative_to(self.prefix.resolve()))
                self.assertTrue(str(config).endswith(suffix))

    def test_parquet_choice_creates_separate_environment(self):
        self.install()
        old = self.active()
        self.install(None, "--parquet")
        self.assertNotEqual(old, self.active())
        sync = [json.loads(line)["args"] for line in self.log.read_text().splitlines()
                if json.loads(line)["args"][0] == "sync"]
        self.assertNotIn("--extra", sync[0])
        self.assertEqual(sync[-1][sync[-1].index("--extra") + 1], "parquet")

    def test_interruption_and_concurrent_install_leave_old_version_working(self):
        self.install()
        old = self.active()
        self.archive("0.2.0")
        marker = self.base / "waiting"
        process = subprocess.Popen(self.command("0.2.0"), env=dict(self.env, FAKE_WAIT=str(marker)),
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                   cwd=self.base, start_new_session=True)
        try:
            deadline = time.monotonic() + 10
            while not marker.exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(marker.exists(), "Installer did not reach preparation")
            result = self.install("0.2.0", ok=False)
            self.assertIn("holds", result.stderr)
            self.assertEqual(self.active(), old)
            os.killpg(process.pid, signal.SIGTERM)
            process.communicate(timeout=10)
            self.assertEqual(self.active(), old)
            self.assertEqual(self.app(), "FAKE_APP " + self.version)
            self.assert_clean()
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.communicate(timeout=10)

    def test_pointer_publication_failure_keeps_previous_app(self):
        self.install()
        old = self.active()
        self.archive("0.2.0")
        real_mv = shutil.which("mv")
        wrapper = self.tools / "mv"
        wrapper.write_text(f"#!{sys.executable}\nimport os,sys\n"
                           "if sys.argv[-1].endswith('/active'): raise SystemExit(8)\n"
                           f"os.execv({real_mv!r},[{real_mv!r},*sys.argv[1:]])\n")
        wrapper.chmod(0o700)
        self.install("0.2.0", ok=False)
        self.assertEqual(self.active(), old)
        self.assertEqual(self.app(), "FAKE_APP " + self.version)
        self.assert_clean()

    def test_unsupported_architecture_is_refused_before_writes(self):
        uname = self.tools / "uname"
        uname.write_text("#!/bin/sh\ncase \"$1\" in -s) echo Linux;; -m) echo riscv64;; esac\n")
        uname.chmod(0o700)
        self.install(ok=False)
        self.assertFalse(self.prefix.exists())
        self.assertFalse(self.log.exists())

    def test_darwin_arm64_branch_with_local_tool(self):
        for name, body in (("uname", 'case "$1" in -s) echo Darwin;; -m) echo arm64;; esac'),
                           ("sw_vers", "echo 12.0")):
            script = self.tools / name
            script.write_text("#!/bin/sh\n" + body + "\n")
            script.chmod(0o700)
        self.install()
        release = self.prefix / "versions" / self.active().strip() / "release"
        self.assertTrue(release.read_text().endswith("aarch64-apple-darwin\n"))

    def curl_double(self):
        path = self.tools / "curl"
        path.write_text(f"#!{sys.executable}\n" + FAKE_CURL)
        path.chmod(0o700)
        return dict(self.env, FAKE_RELEASE=str(self.release), FAKE_LATEST=self.version,
                    FAKE_CURL_LOG=str(self.base / "curl.log"))

    def test_latest_is_resolved_once_then_assets_are_version_pinned(self):
        env = self.curl_double()
        command = ["sh", str(ROOT / "install.sh"), "--csv", "--uv", str(self.uv)]
        result = subprocess.run(command, env=env, cwd=self.base,
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("redirected", result.stdout)
        calls = [json.loads(line) for line in (self.base / "curl.log").read_text().splitlines()]
        urls = [call[-1] for call in calls]
        self.assertEqual(sum(url.endswith('/latest') for url in urls), 1)
        for url in urls[1:]:
            self.assertIn(f"/releases/download/v{self.version}/", url)
        for call in calls:
            self.assertEqual(call[call.index('--proto') + 1], '=https')
            self.assertEqual(call[call.index('--proto-redir') + 1], '=https')

    def test_bootstrap_checksum_failure_does_not_execute_download(self):
        command = self.command()
        at = command.index('--uv')
        del command[at:at+2]
        result = subprocess.run(command, env=self.curl_double(), capture_output=True,
                                cwd=self.base, text=True, timeout=20)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('uv bootstrap checksum mismatch', result.stderr)
        self.assertFalse(self.log.exists())
        self.assertFalse((self.prefix / 'active').exists())
        self.assert_clean()

    def test_invalid_input_is_refused_before_installation(self):
        for extra in (("--prefix", "/"), ("--prefix", "relative"),
                      ("--repository", "one/two/three"), ("--version", "../bad"),
                      ("--prefix", str(self.home / "missing/..")),
                      ("--bin-dir", "bad\nname"), ("--prefix",)):
            with self.subTest(extra=extra):
                self.install(None, *extra, ok=False)
                self.assertFalse(self.prefix.exists())

    def test_trailing_slash_does_not_bypass_root_symlink_refusal(self):
        target = self.home / 'foreign directory'
        target.mkdir()
        (target / 'keep').write_text('foreign data')
        self.prefix.parent.mkdir(parents=True)
        self.prefix.symlink_to(target, target_is_directory=True)
        self.install(None, '--prefix', str(self.prefix) + '///', ok=False)
        self.assertEqual((target / 'keep').read_text(), 'foreign data')
        self.assertFalse((target / '.abs-census-etl').exists())
        self.assertFalse(self.log.exists())

    def test_local_uv_must_match_pinned_version(self):
        result = self.install(ok=False, env=dict(self.env, FAKE_UV_VERSION="0.12.20"))
        self.assertIn('requires uv 0.12.21', result.stderr)
        self.assertFalse((self.prefix / 'active').exists())
        self.assert_clean()

    def test_default_is_csv(self):
        command = self.command()
        result = subprocess.run(command, env=self.env, cwd=self.base,
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        selected = self.prefix / 'versions' / self.active().strip() / 'release'
        self.assertIn(' csv ', selected.read_text())

    def test_custom_paths_are_literal_data_and_shell_arguments_are_forwarded(self):
        self.prefix = self.home / "prefix $(touch SHOULD_NOT_EXIST) '"
        self.bin = self.home / "bin space '"
        self.install(None, '--prefix', str(self.prefix), '--bin-dir', str(self.bin))
        self.assertEqual(self.app(), 'FAKE_APP ' + self.version)
        arguments = ['--echo-args', 'value with spaces', "literal 'quoted' argument"]
        output = subprocess.check_output([str(self.bin / 'abs-census-etl'), *arguments],
                                         text=True, env=self.env, cwd=self.base)
        self.assertEqual(json.loads(output), arguments)
        self.assertFalse((self.base / 'SHOULD_NOT_EXIST').exists())
        self.assert_clean()

    def test_older_platforms_are_refused_before_writes(self):
        for system, arch, name, body in (
            ('Linux','x86_64','getconf','echo "glibc 2.27"'),
            ('Linux','aarch64','getconf','exit 1'),
            ('Darwin','arm64','sw_vers','echo 11.0')):
            with self.subTest(system=system, arch=arch, body=body):
                uname = self.tools / 'uname'
                uname.write_text('#!/bin/sh\ncase "$1" in '
                                 f'-s) echo {system};; -m) echo {arch};; esac\n')
                uname.chmod(0o700)
                probe = self.tools / name
                probe.write_text('#!/bin/sh\n' + body + '\n')
                probe.chmod(0o700)
                self.install(ok=False)
                self.assertFalse(self.prefix.exists())
                self.assertFalse(self.log.exists())
                probe.unlink()

    def test_stale_lock_is_preserved_and_active_app_still_runs(self):
        self.install()
        old = self.active()
        lock = self.prefix / '.install-lock'
        lock.mkdir()
        (lock / 'pid').write_text('999999999\n')
        self.archive('0.2.0')
        result = self.install('0.2.0', ok=False)
        self.assertIn('Inspect it before retrying', result.stderr)
        self.assertEqual((lock / 'pid').read_text(), '999999999\n')
        self.assertEqual(self.active(), old)
        self.assertEqual(self.app(), 'FAKE_APP ' + self.version)

    def test_failed_remote_download_preserves_active_app(self):
        self.install()
        old = self.active()
        calls = self.log.read_text()
        self.archive('0.2.0')
        command = ['sh', str(ROOT / 'install.sh'), '--csv', '--version', '0.2.0',
                   '--uv', str(self.uv)]
        for asset in ('SHA256SUMS', 'abs_census_etl-0.2.0.tar.gz'):
            with self.subTest(asset=asset):
                env = dict(self.curl_double(), FAKE_FAIL_DOWNLOAD=asset)
                result = subprocess.run(command, env=env, cwd=self.base,
                                        capture_output=True, text=True, timeout=20)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('Download failed', result.stderr)
                self.assertEqual(self.active(), old)
                self.assertEqual(self.app(), 'FAKE_APP ' + self.version)
                self.assertEqual(self.log.read_text(), calls)
                self.assert_clean()

    def test_repeating_command_repairs_a_damaged_active_environment(self):
        self.install()
        old = self.active().strip()
        python = self.prefix / 'versions' / old / 'env/bin/python'
        python.write_text('#!/bin/sh\nexit 9\n')
        self.install()
        self.assertNotEqual(self.active().strip(), old)
        self.assertEqual(self.app(), 'FAKE_APP ' + self.version)
        self.assert_clean()

    def test_signal_after_pointer_rename_never_deletes_the_selected_environment(self):
        self.install()
        old = self.active()
        self.archive('0.2.0')
        real_mv = shutil.which('mv')
        wrapper = self.tools / 'mv'
        wrapper.write_text(f'#!{sys.executable}\nimport os,signal,subprocess,sys,time\n'
                           f'result=subprocess.run([{real_mv!r},*sys.argv[1:]])\n'
                           'if result.returncode==0 and sys.argv[-1].endswith("/active"):\n'
                           '    os.kill(os.getppid(),signal.SIGTERM)\n'
                           '    time.sleep(0.1)\n'
                           'raise SystemExit(result.returncode)\n')
        wrapper.chmod(0o700)
        self.install('0.2.0', '--parquet', '--repository', 'owner/fork', ok=False)
        self.assertNotEqual(self.active(), old)
        self.assertEqual(self.app(), 'FAKE_APP 0.2.0')
        selected = self.prefix / 'versions' / self.active().strip()
        self.assertTrue((selected / 'env/bin/python').is_file())
        self.assert_clean()

        wrapper.unlink()
        self.install('0.2.0')
        settings = (self.prefix / 'settings').read_text()
        self.assertIn('format=parquet', settings)
        self.assertIn('repository=owner/fork', settings)

    def test_readme_download_command_forwards_options_and_refreshes_installer(self):
        command = next(line for line in (ROOT / 'README.md').read_text().splitlines()
                       if line.startswith("sh -c 'installer="))
        saved = self.release / 'install.sh'
        saved.write_text((ROOT / 'install.sh').read_text())
        env = self.curl_double()
        arguments = ['--version', self.version, '--release-dir', str(self.release),
                     '--uv', str(self.uv)]
        result = subprocess.run(['sh', '-c', command + ' "$@"', 'readme-probe',
                                 *arguments, '--parquet'], env=env, cwd=self.base,
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('format=parquet', (self.prefix / 'settings').read_text())
        # A changed freshly downloaded script is executed on the next command.
        saved.write_text(saved.read_text().replace('set -eu',
                         "set -eu\nprintf 'FRESH_INSTALLER\\n'", 1))
        result = subprocess.run(['sh', '-c', command + ' "$@"', 'readme-probe',
                                 *arguments], env=env, cwd=self.base,
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('FRESH_INSTALLER', result.stdout)
        self.assertIn('Already installed', result.stdout)
        self.assertIn('format=parquet', (self.prefix / 'settings').read_text())

    def test_options_survive_fresh_no_option_updates_and_pointer_damage(self):
        self.install(None, '--parquet')
        first = self.active()
        for pointer in ('', 'invalid/name\n', 'not-a-release\n'):
            (self.prefix / 'active').write_text(pointer)
            self.install()
            self.assertEqual(self.active(), first)
        self.archive('0.2.0')
        self.install('0.2.0')
        selected = self.prefix / 'versions' / self.active().strip()
        self.assertIn(' parquet ', (selected / 'release').read_text())
        self.assertIn('format=parquet', (self.prefix / 'settings').read_text())
        self.install('0.2.0', '--csv')
        self.assertIn('format=csv', (self.prefix / 'settings').read_text())

    def test_rollback_format_switch_and_bin_move_reuse_without_downloads(self):
        self.install()
        csv = self.active()
        self.install(None, '--parquet')
        parquet = self.active()
        calls = self.log.read_text()
        shutil.rmtree(self.release)
        self.release.mkdir()
        self.install(None, '--csv')
        self.assertEqual(self.active(), csv)
        self.install(None, '--parquet')
        self.assertEqual(self.active(), parquet)
        self.bin = self.home / 'new bin'
        self.install(None, '--bin-dir', str(self.bin))
        self.assertEqual(self.active(), parquet)
        self.assertEqual(self.log.read_text(), calls)
        self.assertEqual(self.app(), 'FAKE_APP ' + self.version)
        self.assertEqual(len(list((self.prefix / 'versions').iterdir())), 2)
        self.install()
        self.assertIn('bin_dir=' + str(self.bin.resolve()), (self.prefix / 'settings').read_text())

    def test_repository_identity_is_saved_and_separates_cached_environments(self):
        self.install(None, '--repository', 'owner/fork')
        first = self.active()
        again = self.install()
        self.assertIn('Already installed', again.stdout)
        self.assertIn('Using non-default release repository: owner/fork', again.stdout)
        self.assertLess(again.stdout.index('owner/fork'), again.stdout.index('Already installed'))
        self.assertIn('repository=owner/fork', (self.prefix / 'settings').read_text())
        changed = self.install(None, '--repository', 'different/fork')
        self.assertIn('Using non-default release repository: different/fork', changed.stdout)
        self.assertNotEqual(self.active(), first)
        reset = self.install(None, '--repository', 'Agent-Cognitive-Enterprise/abs-census-etl')
        self.assertNotIn('Using non-default', reset.stdout)
        self.assertIn('repository=Agent-Cognitive-Enterprise/abs-census-etl',
                      (self.prefix / 'settings').read_text())

    def test_saved_fork_is_announced_before_latest_resolution(self):
        self.install(None, '--repository', 'owner/fork')
        env = self.curl_double()
        result = subprocess.run(['sh', str(ROOT / 'install.sh'), '--uv', str(self.uv)],
                                env=env, cwd=self.base, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Using non-default release repository: owner/fork', result.stdout)
        self.assertLess(result.stdout.index('owner/fork'),
                        result.stdout.index('Already installed'))
        calls = [json.loads(line)[-1]
                 for line in (self.base / 'curl.log').read_text().splitlines()]
        self.assertEqual(calls, ['https://github.com/owner/fork/releases/latest'])

    def test_invalid_versions_are_rejected_before_managed_writes(self):
        for version in ('1.0.0-rc1', '1.0RC1', '01.2', '1.0_post1', '1.0+ABC',
                        '1.0+01', '0!1.0', '1.0+local-thing', '1\n2', '1!1.0', '1.0+local.2'):
            with self.subTest(version=version):
                self.install(version, ok=False)
                self.assertFalse(self.prefix.exists())

    def test_canonical_public_version_redirect_after_repository_rename(self):
        version = '1.0.0rc1.post2.dev3'
        self.archive(version)
        env = dict(self.curl_double(), FAKE_LATEST_URL=
                   'https://github.com/new-owner/renamed/releases/tag/v' +
                   version)
        result = subprocess.run(['sh', str(ROOT / 'install.sh'), '--uv', str(self.uv)],
                                env=env, cwd=self.base, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.app(), 'FAKE_APP ' + version)
        notice = ('Latest release redirected from Agent-Cognitive-Enterprise/abs-census-etl '
                  'to new-owner/renamed')
        self.assertIn(notice, result.stdout)
        self.assertLess(result.stdout.index(notice), result.stdout.index('Installed'))
        urls = [json.loads(line)[-1] for line in (self.base / 'curl.log').read_text().splitlines()]
        self.assertTrue(all('/new-owner/renamed/releases/download/' in url for url in urls[1:]))

    def test_actual_installed_version_is_checked_not_just_source_metadata(self):
        result = self.install(ok=False, env=dict(self.env, FAKE_INSTALLED_VERSION='9.9.9'))
        self.assertIn('Installed project version differs', result.stderr)
        self.assertFalse((self.prefix / 'active').exists())

    def test_wheel_checksum_is_checked_before_any_tool_execution(self):
        wheel = next(self.release.glob('*.whl'))
        wheel.write_bytes(wheel.read_bytes() + b'damaged')
        result = self.install(ok=False)
        self.assertIn('checksum mismatch', result.stderr)
        self.assertFalse(self.log.exists())

    def test_bad_local_options_do_not_create_state_or_download(self):
        for extra in (('--release-dir', str(self.release)),
                      ('--version', self.version, '--uv', 'relative'),
                      ('--version', self.version, '--uv', '/missing')):
            result = subprocess.run(['sh', str(ROOT / 'install.sh'), *extra],
                                    cwd=self.base, env=self.env, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(self.prefix.exists())
            self.assertFalse(self.log.exists())

    def test_raw_prefix_control_characters_are_rejected_without_side_effects(self):
        for suffix in ('\n', '\n\n', '\r', '\r\n', '/\n'):
            with self.subTest(suffix=repr(suffix)):
                result = self.install(None, '--prefix', str(self.prefix) + suffix, ok=False)
                self.assertIn('Paths must not contain', result.stderr)
                self.assertFalse(self.prefix.exists())
                self.assertFalse(self.bin.exists())
                self.assertFalse(self.log.exists())

    def test_encoded_local_and_epoch_latest_tags_are_rejected(self):
        for version in ('1.0%2Blocal', '1%211.0', '1.0+local', '1!1.0'):
            with self.subTest(version=version):
                env = dict(self.curl_double(), FAKE_LATEST_URL=
                           'https://github.com/owner/repo/releases/tag/v' + version)
                result = subprocess.run(['sh', str(ROOT / 'install.sh'), '--uv', str(self.uv)],
                                        cwd=self.base, env=env, capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('invalid version', result.stderr)
                self.assertFalse(self.log.exists())
                calls = (self.base / 'curl.log').read_text().splitlines()
                self.assertTrue(all(json.loads(line)[-1].endswith('/latest') for line in calls))

    def test_backslashes_in_root_do_not_change_checksum(self):
        self.prefix = self.home / 'back\\slash'
        self.install(None, '--prefix', str(self.prefix))
        self.assertEqual(self.app(), 'FAKE_APP ' + self.version)

    def test_shared_ancestors_follow_caller_umask(self):
        result = subprocess.run(self.command(), env=self.env, cwd=self.base,
                                capture_output=True, text=True, umask=0o022)
        self.assertEqual(result.returncode, 0, result.stderr)
        for folder in (self.home / '.local', self.home / '.local/share', self.bin):
            self.assertEqual(folder.stat().st_mode & 0o777, 0o755)
        self.assertEqual(self.prefix.stat().st_mode & 0o777, 0o700)

    def test_network_opt_in_does_not_restore_build_or_target_overrides(self):
        env = dict(self.env, UV_INDEX_URL='https://mirror.example/simple',
                   PIP_INDEX_URL='https://other-mirror.example/simple',
                   UV_CA_BUNDLE='/not-a-supported-certificate-setting',
                   PIP_CERT='/trusted/company ca.pem', UV_SYSTEM_CERTS='true',
                   UV_PROJECT_ENVIRONMENT='/wrong', UV_OVERRIDE='/wrong',
                   HTTPS_PROXY='http://proxy.example:3128')
        self.install(None, '--use-network-env', env=env)
        for call in map(json.loads, self.log.read_text().splitlines()):
            actual = call['env']
            self.assertNotIn('UV_INDEX_URL', actual)
            self.assertNotIn('PIP_INDEX_URL', actual)
            self.assertNotIn('UV_CA_BUNDLE', actual)
            self.assertEqual(actual['SSL_CERT_FILE'], env['PIP_CERT'])
            self.assertEqual(actual['UV_SYSTEM_CERTS'], 'true')
            self.assertEqual(actual['HTTPS_PROXY'], env['HTTPS_PROXY'])
            self.assertNotIn('UV_PROJECT_ENVIRONMENT', actual)
            self.assertNotIn('UV_OVERRIDE', actual)

    def test_environment_link_failure_preserves_old_install(self):
        self.install()
        old = self.active()
        self.archive('0.2.0')
        link = self.tools / 'ln'
        link.write_text('#!/bin/sh\nexit 9\n')
        link.chmod(0o700)
        result = self.install('0.2.0', ok=False)
        self.assertIn('Cannot link', result.stderr)
        self.assertEqual(self.active(), old)
        self.assert_clean()

    def test_owned_launcher_for_another_root_names_the_real_problem(self):
        self.install()
        result = self.install(None, '--prefix', str(self.home / 'second root'), ok=False)
        self.assertIn('another installation', result.stderr)
        self.assertIn('--bin-dir', result.stderr)
        self.assertEqual(self.app(), 'FAKE_APP ' + self.version)


if __name__ == "__main__":
    unittest.main()
