"""Online smoke of the real shell installer against local release artifacts.

Builds test-only release versions, bootstraps pinned uv/managed Python and
installs into a temporary HOME. Never publishes, edits profiles, changes the
working .venv or installs into the real user's application directories.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import runpy
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]


def run(args, *, cwd, env, expected=0):
    result = subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True, timeout=300)
    if result.returncode != expected:
        raise RuntimeError(f"Failed: {args}\n{result.stdout}{result.stderr}")
    return result.stdout + result.stderr


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release-dir', type=Path, help='Verified current release bundle')
    args = parser.parse_args()
    if os.name != "posix" or getattr(os, "geteuid", lambda: 0)() == 0:
        print("Installer smoke requires a normal POSIX user.", file=sys.stderr)
        return 2
    uv = shutil.which("uv")
    if uv is None:
        print("Artifact preparation requires uv on PATH.", file=sys.stderr)
        return 2
    helpers = runpy.run_path(str(ROOT / "tests/check_packaging.py"))
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text())
    version = metadata["project"]["version"]
    # A distinct canonical public prerelease, not a local/epoch version.
    release_base = re.sub(r"\.dev[0-9]+$", "", version)
    post = re.search(r"\.post([0-9]+)$", release_base)
    newer = (release_base[:post.start()] + f".post{int(post[1]) + 1}"
             if post else release_base + ".post1")
    with tempfile.TemporaryDirectory(prefix="abs installer smoke '") as td:
        base = Path(td)
        # Preserve project configuration while blocking shared-temp parent configs.
        scratch = helpers["guarded_workspace"](base)
        env = helpers["isolated_environment"](dict(os.environ))
        uv_env = helpers["uv_environment"](env, scratch / "config")
        # Execute the actual installer's resolver against public v-tagged releases of
        # dependencies, not a curl double. One must resolve, so a third-party tag change
        # cannot fail the check alone; a broken resolver fails all. App asset transport
        # is covered by real uv bootstrap.
        installer = (ROOT / "install.sh").read_text()
        validator = re.search(r"valid_version\(\) \{.*?\n\}", installer, re.S).group()
        resolver = installer.split("# BEGIN release resolver\n")[1].split(
            "# END release resolver")[0]
        failures = []
        for project in ("pandas-dev/pandas", "numpy/numpy", "psf/requests"):
            probe = ("die() { printf '%s\\n' \"$*\" >&2; exit 1; }\n" + validator + "\n" +
                     resolver + f"\nresolve_latest {project}\n" +
                     "printf '%s %s\\n' \"$release_repo\" \"$VERSION\"\n")
            result = subprocess.run(["sh", "-c", probe], cwd=scratch, env=env,
                                    capture_output=True, text=True, timeout=120)
            resolved = result.stdout + result.stderr
            if result.returncode == 0 and resolved.startswith(project + " "):
                break
            failures.append(f"{project}: {resolved.strip()}")
        else:
            raise RuntimeError("Live latest resolution failed for every project:\n" +
                               "\n".join(failures))
        for failure in failures:
            print(f"NOTE: live resolver fallback after {failure}", flush=True)
        print(f"PASS: live GitHub latest redirect ({project}) parsed by the real installer "
              "resolver", flush=True)
        source = scratch / "build-source"
        helpers["copy_build_source"](ROOT, source)
        assets = scratch / "assets"
        assets.mkdir()
        if args.release_dir:
            release_tools = runpy.run_path(str(ROOT / 'scripts/release.py'))
            release_tools['verify'](args.release_dir, 'v' + version)
            for name in ('install.sh', 'install.ps1'):
                if (args.release_dir / name).read_bytes() != (ROOT / name).read_bytes():
                    raise RuntimeError('Release installer differs from checkout: ' + name)
            for name in release_tools['asset_names'](version):
                if name.endswith(('.tar.gz', '.whl')):
                    shutil.copyfile(args.release_dir / name, assets / name)
        for release in ((newer,) if args.release_dir else (version, newer)):
            for filename in ("pyproject.toml", "uv.lock"):
                original = (ROOT / filename).read_text()
                (source / filename).write_text(original.replace(
                    f'version = "{version}"', f'version = "{release}"', 1))
            run([uv, "build", "--python", sys.executable,
                 "--out-dir", str(assets)], cwd=source, env=uv_env)
        checksums = "".join(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n"
                            for p in sorted(assets.iterdir()))
        (assets / "SHA256SUMS").write_bytes(checksums.encode("ascii"))
        home = scratch / "home"
        home.mkdir()
        env = dict(env, HOME=str(home), PATH="/usr/bin:/bin")
        work = scratch / "user data"
        work.mkdir()
        prefix = home / ".local/share/abs-census-etl"
        launcher = home / ".local/bin/abs-census-etl"
        command = ["sh", str(ROOT / "install.sh"), "--release-dir", str(assets)]
        first_command = (["sh", str(args.release_dir.resolve() / "install.sh"),
                          "--release-dir", str(args.release_dir.resolve())]
                         if args.release_dir else command)
        run([*first_command, "--csv", "--version", version], cwd=work, env=env)
        help_text = run([str(launcher), "--help"], cwd=work, env=env)
        if ("usage: abs-census-etl " not in help_text or
                "    abs-census-etl --geo POA" not in help_text or
                "python abs_census_etl.py" in help_text):
            raise RuntimeError(help_text)
        bad_option = run([str(launcher), "--unknown-option"], cwd=work, env=env, expected=2)
        if "abs-census-etl: error:" not in bad_option:
            raise RuntimeError(bad_option)
        # Exercise the actual uv wrapper with poisoned index variables. A real
        # mirror override makes --locked stale; this must pass without network.
        runner = re.search(r"run_uv\(\) \(\n.*?\n\)", installer, re.S).group()
        isolated = scratch / "network-isolation"
        isolated.mkdir()
        isolated.joinpath("uv.toml").write_text("")
        script = (runner + "\nwork=$1; PREFIX=$2; uv=$3; uv_cwd=$4\n" +
                  "NETWORK_ENV=1; network_ca=; network_system=\n" +
                  'run_uv lock --check --offline --no-python-downloads --python "$5"\n')
        poisoned = dict(env, UV_INDEX_URL="https://mirror.invalid/simple",
                        PIP_INDEX_URL="https://other-mirror.invalid/simple",
                        UV_OVERRIDE="/missing-override", PIP_CONSTRAINT="/missing-constraint")
        run(["sh", "-c", script, "uv-isolation", str(isolated), str(prefix),
             str(prefix / "tools/uv-0.12.21"), str(source), sys.executable],
            cwd=scratch, env=poisoned)
        # Changing only index passthrough forces offline dependency resolution;
        # the positive control above checks the same source/lock without it.
        weakened = script.replace('    unset VIRTUAL_ENV',
                                  '    export UV_INDEX_URL=https://mirror.invalid/simple\n'
                                  '    unset VIRTUAL_ENV')
        control = subprocess.run(
            ["sh", "-c", weakened, "uv-isolation", str(isolated), str(prefix),
             str(prefix / "tools/uv-0.12.21"), str(source), sys.executable],
            cwd=scratch, env=poisoned, capture_output=True, text=True, timeout=30)
        details = control.stdout + control.stderr
        if control.returncode == 0 or not all(message in details for message in (
                "No solution found", "network was disabled")):
            raise RuntimeError("Mirror negative control did not fail as intended: " + details)
        print("PASS: real pinned uv isolates indexes; mirror negative control fails offline",
              flush=True)
        active = (prefix / "active").read_text().strip()
        print("PASS: first install bootstraps pinned uv and managed Python in a private HOME",
              flush=True)
        repeated = run([*command, "--csv", "--version", version], cwd=work, env=env)
        if "Already installed" not in repeated:
            raise RuntimeError(repeated)
        with ZipFile(work / "nt.zip", "w") as archive:
            archive.writestr("2021 Census GCP Postal Areas for NT/2021Census_G01_NT_POA.csv",
                             "POA_CODE_2021,Tot_P_P\n0800,100\n0810,400\n")
        run([str(launcher), "--geo", "POA", "--state", "NT", "--zip", "nt.zip",
             "--codes", "0800", "--out", "exports"], cwd=work, env=env)
        data = work / "exports/2021_GCP_POA_NT_G01_wide.csv"
        with data.open(newline="") as stream:
            rows = list(csv.DictReader(stream))
        if len(rows) != 1 or rows[0]["POA_CODE_2021"] != "0800":
            raise RuntimeError(rows)
        before = data.read_bytes()
        print("PASS: repeat install skips; installed launcher exports offline CSV "
              "with leading zero", flush=True)
        run([*command, "--csv", "--version", newer], cwd=work, env=env)
        current = (prefix / "active").read_text().strip()
        if current == active:
            raise RuntimeError("Update did not change the active version")
        for slot, expected in ((active, version), (current, newer)):
            python = prefix / "versions" / slot / "env/bin/python"
            output = run([str(python), "-I", "-c",
                          "import importlib.metadata as m; print(m.version('abs-census-etl'))"],
                         cwd=work, env=env)
            if output.strip() != expected:
                raise RuntimeError(output)
        if data.read_bytes() != before:
            raise RuntimeError("User export changed during update")
        print("PASS: update activates new release and retains working previous environment/data",
              flush=True)
        (assets / "SHA256SUMS").write_text("0" * 64 + f"  abs_census_etl-{version}.tar.gz\n")
        failure = run([*command, "--parquet", "--version", version], cwd=work, env=env, expected=1)
        if "checksum mismatch" not in failure or (prefix / "active").read_text().strip() != current:
            raise RuntimeError(failure)
        run([str(launcher), "--help"], cwd=work, env=env)
        (assets / "SHA256SUMS").write_bytes(checksums.encode("ascii"))
        run([*command, "--parquet", "--version", newer], cwd=work, env=env)
        run([str(launcher), "--geo", "POA", "--state", "NT", "--zip", "nt.zip",
             "--codes", "0800", "--format", "parquet", "--out", "parquet"], cwd=work, env=env)
        manifest = json.loads((work / "parquet/2021_GCP_POA_NT_manifest.json").read_text())
        if manifest["tables"]["G01"]["rows"] != 1 or data.read_bytes() != before:
            raise RuntimeError("Parquet result or retained data differs")
        # A plain update retains Parquet; an explicit cached rollback needs no assets.
        repeated = run([*command, "--version", newer], cwd=work, env=env)
        if "Already installed" not in repeated:
            raise RuntimeError("Update lost saved Parquet choice: " + repeated)
        shutil.rmtree(assets)
        assets.mkdir()
        run([*command, "--csv", "--version", version], cwd=work, env=env)
        if (prefix / "active").read_text().strip() != active:
            raise RuntimeError("Cached rollback did not reuse the original environment")
        if (prefix / ".install-lock").exists() or list(prefix.glob(".install.*")):
            raise RuntimeError("Installer left work/lock state behind")
        if list((home / ".local/bin").glob(".abs-census-etl.*")):
            raise RuntimeError("Installer left launcher staging files")
        print("PASS: checksum failure preserves app; Parquet, saved choice "
              "and cached rollback work", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
