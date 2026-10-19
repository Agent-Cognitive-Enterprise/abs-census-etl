"""Build temporary real releases and run native Windows installer checks.

Requires development Python 3.14 and uv; application installation itself does
not require either on PATH. No real profile, installation or project venv changes.
"""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import re
import runpy
import shutil
import subprocess
import sys
import tempfile
import tomllib

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uv", help="Trusted uv executable for preparing test release artifacts")
    args = parser.parse_args()
    if os.name != "nt":
        print("This smoke requires native Windows; use tests/check_installer.py on Unix.",
              file=sys.stderr)
        return 2
    uv = args.uv or shutil.which("uv")
    if not uv:
        print("Artifact preparation needs uv on PATH or --uv ABSOLUTE_PATH.", file=sys.stderr)
        return 2
    powershell = Path(os.environ["WINDIR"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    helpers = runpy.run_path(str(ROOT / "tests/check_packaging.py"))
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text())
    version = metadata["project"]["version"]
    release = re.sub(r"\.dev[0-9]+$", "", version)
    post = re.search(r"\.post([0-9]+)$", release)
    newer = release[:post.start()] + f".post{int(post[1]) + 1}" if post else release + ".post1"
    with tempfile.TemporaryDirectory(prefix="abs-windows-smoke-") as td:
        scratch = helpers["guarded_workspace"](Path(td))
        source = scratch / "source"
        helpers["copy_build_source"](ROOT, source)
        env = helpers["isolated_environment"](dict(os.environ))
        uv_env = helpers["uv_environment"](env, scratch / "config")
        assets = scratch / "assets"
        for selected in (version, newer):
            for name in ("pyproject.toml", "uv.lock"):
                text = (ROOT / name).read_text()
                (source / name).write_text(text.replace(
                    f'version = "{version}"', f'version = "{selected}"', 1))
            helpers["run"]([uv, "build", "--python", sys.executable,
                            "--out-dir", str(assets)], cwd=source, env=uv_env)
        sums = "".join(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n"
                       for p in sorted(assets.iterdir()))
        (assets / "SHA256SUMS").write_text(sums)
        result = subprocess.run(
            [str(powershell), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
             str(ROOT / "tests/check_windows_installer.ps1"), "-ReleaseDir", str(assets),
             "-Version", version, "-NextVersion", newer], cwd=ROOT, env=env,
            timeout=600,
        )
        return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
