"""Build/install smoke checks; requires uv and may download dependencies.

Run with the project Python. Environments/artifacts are temporary. The real
checkout build creates build/ and project egg-info while running, then removes
them; pre-existing directories are refused. The user's .venv and index are
left alone.
"""
from __future__ import annotations

import csv
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
SOURCE_FILES = {
    "abs_census_etl.py", "pyproject.toml", "uv.lock", "MANIFEST.in", "LICENSE",
    "README.md", "CHANGELOG.md", "setup.cfg", "tests/check_packaging.py",
    "docs/installation.md", "docs/data-guide.md", "docs/development.md",
    "docs/release-assets.md",
    "tests/check_mutations.py", "tests/test_abs_census_etl.py",
    "tests/test_packaging_checks.py", "tests/test_data_features.py",
    "tests/check_abs_data.py", "tests/test_year_coverage.py",
    "tests/check_compat.py", "tests/fixtures/v020_manifest.json",
}
# Placeholders for files a clone or working checkout has but releases must not
# ship. Writing them copies no checkout data, and the exact sdist list then
# catches MANIFEST.in or setuptools changes that would include them. No LICENSE
# decoy: setuptools ships license files by default, as a chosen license should.
DECOY_FILES = (
    "tests/test_scratch_do_not_ship.py",  # setuptools auto-includes tests/test*.py
    "tests/helper_do_not_ship.py",
    "tests/fixture_do_not_ship.csv",
    "AGENTS.md",
    ".gitignore",
    "docs/progress/do_not_ship.md",
    "docs/release-readiness.md",
    "data.zip",
    "abs_out/raw/do_not_ship.zip",
    "abs_cache/do_not_ship.zip",
)
# Stands in for a uv.toml another local user could plant in a shared temp
# directory such as /tmp. uv searches parent directories for configuration, so
# every run plants this above the work folder; the empty guard file there must
# end the search. Reading it fails at once: no uv matches, and .invalid never
# resolves.
PLANTED_PARENT_CONFIG = ('required-version = "==0.0.1"\n'
                         'extra-index-url = ["https://planted-index.invalid/simple"]\n')


def require(condition: bool, detail: object) -> None:
    if not condition:
        raise RuntimeError(str(detail))


def check_files(actual: set[str], expected: set[str], kind: str) -> None:
    require(actual == expected,
            f"Unexpected {kind} contents: extra={sorted(actual - expected)}, "
            f"missing={sorted(expected - actual)}")


def copy_build_source(source: Path, destination: Path) -> None:
    """Copy reviewed build inputs without traversing unrelated data or links."""
    for name in sorted(SOURCE_FILES):
        candidate = source
        for part in Path(name).parts:
            candidate = candidate / part
            require(not candidate.is_symlink(), f"Symlinked build input: {candidate}")
        require(candidate.is_file(), f"Missing build input: {candidate}")
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(candidate, target, follow_symlinks=False)


def write_decoys(destination: Path) -> None:
    for name in DECOY_FILES:
        path = destination / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("raise RuntimeError('decoy: must not ship')\n")


def expected_sdist(version: str) -> set[str]:
    names = SOURCE_FILES | {"PKG-INFO", "setup.cfg"} | {
        f"abs_census_etl.egg-info/{name}" for name in (
            "PKG-INFO", "SOURCES.txt", "dependency_links.txt", "entry_points.txt",
            "requires.txt", "top_level.txt")}
    return {f"abs_census_etl-{version}/{name}" for name in names}


def sdist_files(sdist: Path) -> set[str]:
    with tarfile.open(sdist) as archive:
        members = archive.getmembers()
    require(all(m.isfile() or m.isdir() for m in members),
            "Source archive contains non-file/non-directory entries")
    return {m.name for m in members if m.isfile()}


def run(args: list[str], *, cwd: Path, env: dict[str, str], expected: int = 0) -> str:
    result = subprocess.run(args, cwd=cwd, env=env, capture_output=True,
                            text=True, timeout=180)
    if result.returncode != expected:
        raise RuntimeError(f"Command failed ({result.returncode}): {args}\n"
                           f"{result.stdout}{result.stderr}")
    return result.stdout + result.stderr


def guarded_workspace(scratch: Path) -> Path:
    """Return a work folder whose empty uv.toml hides a planted parent config."""
    (scratch / "uv.toml").write_text(PLANTED_PARENT_CONFIG)
    work = scratch / "work"
    work.mkdir()
    (work / "uv.toml").write_text("")
    return work


def isolated_environment(original: dict[str, str]) -> dict[str, str]:
    return {key: value for key, value in original.items()
            if not key.upper().startswith(("UV_", "PIP_"))
            and key.upper() not in {"VIRTUAL_ENV", "PYTHONPATH", "PYTHONHOME"}}


def uv_environment(env: dict[str, str], config: Path) -> dict[str, str]:
    """Redirect user/system uv config, for uv commands only."""
    for kind in ("user", "system"):
        directory = config / kind / "uv"
        directory.mkdir(parents=True, exist_ok=True)
        # An actual empty file shadows /etc/uv fallback as well as XDG config.
        (directory / "uv.toml").write_text("")
    return dict(env, XDG_CONFIG_HOME=str(config / "user"),
                XDG_CONFIG_DIRS=str(config / "system"),
                APPDATA=str(config / "user"), PROGRAMDATA=str(config / "system"))


def check_project_configuration(uv: list[str], scratch: Path, env: dict[str, str],
                                metadata: dict) -> None:
    # A non-project command sees only the guard, not the planted parent config.
    run([*uv, "venv", "--python", sys.executable, str(scratch / "guard-probe")],
        cwd=scratch, env=env)
    project = scratch / "configuration-probe"
    project.mkdir()
    target = project / "pyproject.toml"
    base = ('[project]\nname = "configuration-probe"\nversion = "0.0.0"\n'
            f'requires-python = "{metadata["project"]["requires-python"]}"\n'
            'dependencies = []\n\n[tool.uv]\n')
    target.write_text(base + 'required-version = ">=99"\n')
    result = subprocess.run([*uv, "sync", "--locked", "--dry-run", "--python", sys.executable],
                            cwd=project, env=env, capture_output=True, text=True, timeout=30)
    require(result.returncode != 0 and ">=99" in result.stdout + result.stderr,
            "Project required-version was not enforced")
    version = metadata["tool"]["uv"]["required-version"]
    target.write_text(base + f'required-version = "{version}"\n'
                      'exclude-newer = "2000-01-01T00:00:00Z"\n')
    run([*uv, "lock", "--python", sys.executable], cwd=project, env=env)
    lock = tomllib.loads((project / "uv.lock").read_text())
    require(str(lock.get("options", {}).get("exclude-newer", "")).startswith("2000-01-01"),
            "Project exclude-newer was not written into the probe lock")
    run([*uv, "sync", "--locked", "--dry-run", "--python", sys.executable], cwd=project, env=env)
    print("PASS: planted parent uv.toml ignored; project required-version enforced; "
          "exclude-newer lock sync accepted", flush=True)


def check_artifacts(artifacts: Path, version: str) -> tuple[Path, Path]:
    wheel, = artifacts.glob("*.whl")
    sdist, = artifacts.glob("*.tar.gz")
    with ZipFile(wheel) as archive:
        info = f"abs_census_etl-{version}.dist-info"
        check_files(set(archive.namelist()), {"abs_census_etl.py"} | {
            f"{info}/{name}" for name in ("METADATA", "WHEEL", "RECORD", "licenses/LICENSE",
                                          "entry_points.txt", "top_level.txt")}, "wheel")
    check_files(sdist_files(sdist), expected_sdist(version), "sdist")
    return wheel, sdist


def build_checkout(uv: list[str], artifacts: Path, env: dict[str, str]) -> None:
    generated = (ROOT / "build", ROOT / "abs_census_etl.egg-info")
    require(not any(p.exists() or p.is_symlink() for p in generated),
            "Remove generated build/ and abs_census_etl.egg-info/ before packaging checks; "
            "existing directories are left untouched.")
    try:
        run([*uv, "build", "--python", sys.executable, "--out-dir", str(artifacts),
             "--default-index", "https://pypi.org/simple"], cwd=ROOT, env=env)
    finally:
        # These paths were absent before this build; never delete an old one.
        for path in generated:
            if path.is_symlink():
                path.unlink()
            elif path.exists():
                shutil.rmtree(path)


def check_pip_rollback(uv: list[str], scratch: Path, uv_env: dict[str, str],
                       env: dict[str, str], source_root: Path) -> None:
    source = scratch / "pip-source"
    copy_build_source(source_root, source)
    environment = scratch / "pip-env"
    run([*uv, "venv", "--seed", "--python", sys.executable, str(environment),
         "--default-index", "https://pypi.org/simple"], cwd=scratch, env=uv_env)
    scripts = environment / ("Scripts" if os.name == "nt" else "bin")
    python = scripts / ("python.exe" if os.name == "nt" else "python")
    install = [str(python), "-m", "pip", "--isolated", "install",
               "--index-url", "https://pypi.org/simple", str(source)]
    run(install, cwd=scratch, env=env)
    stale = source / "build/lib/abs_census_etl.py"
    require(stale.is_file(), "Pip probe did not create the stale build-copy precondition")
    module = source / "abs_census_etl.py"
    old_time = stale.stat().st_mtime - 86400
    module.write_text(module.read_text() + "\n_PACKAGING_ROLLBACK_SENTINEL = 'older-source'\n")
    os.utime(module, (old_time, old_time))
    require("_PACKAGING_ROLLBACK_SENTINEL" not in stale.read_text(),
            "Pip probe's old build copy unexpectedly contains the new marker")
    # Keep the stale copy: project build settings must refresh it automatically.
    run(install, cwd=scratch, env=env)
    probe = ("import abs_census_etl as m; "
             "assert m._PACKAGING_ROLLBACK_SENTINEL == 'older-source'")
    run([str(python), "-I", "-c", probe], cwd=scratch, env=env)
    print("PASS: same pip command installs older-mtime source without build cleanup",
          flush=True)


def smoke(environment: Path, outside: Path, env: dict[str, str],
          version: str, *, parquet: bool, label: str) -> None:
    scripts = environment / ("Scripts" if os.name == "nt" else "bin")
    python = scripts / ("python.exe" if os.name == "nt" else "python")
    launcher = scripts / ("abs-census-etl.exe" if os.name == "nt" else "abs-census-etl")
    probe = """
import importlib.metadata as metadata
import importlib.util
from pathlib import Path
import sys
import abs_census_etl
assert Path(abs_census_etl.__file__).is_relative_to(Path(sys.prefix)), abs_census_etl.__file__
assert metadata.version('abs-census-etl') == sys.argv[1]
assert (importlib.util.find_spec('pyarrow') is not None) == (sys.argv[2] == 'yes')
assert importlib.util.find_spec('fastparquet') is None
"""
    run([str(python), "-I", "-c", probe, version, "yes" if parquet else "no"],
        cwd=outside, env=env)
    for command in ([str(launcher)], [str(python), "-I", "-m", "abs_census_etl"]):
        help_text = run([*command, "--help"], cwd=outside, env=env)
        require("usage: abs-census-etl " in help_text and
                "    abs-census-etl --geo POA" in help_text and
                "python abs_census_etl.py" not in help_text,
                f"Incorrect CLI help: {command}: {help_text}")
        bad_option = run([*command, "--unknown-option"], cwd=outside, env=env, expected=2)
        require("usage: abs-census-etl " in bad_option and
                "abs-census-etl: error:" in bad_option, bad_option)
        failure = run([*command, "--geo", "POA", "--state", "NT", "--zip", "nt.zip",
                       "--codes", "bad"],
                      cwd=outside, env=env, expected=1)
        require("ERROR:" in failure and "Traceback" not in failure, failure)
        columns = run([*command, "--geo", "POA", "--state", "NT", "--year", "2016",
                       "--zip", "nt2016.zip", "--list-columns"], cwd=outside, env=env)
        require("Tot_P_P" in columns and "POA_CODE_2016" not in columns, columns)
        wrong_year = run([*command, "--geo", "POA", "--state", "NT", "--zip",
                          "nt2016.zip", "--list-columns"], cwd=outside, env=env, expected=1)
        require("Wrong Census year" in wrong_year and "--year 2016" in wrong_year
                and "Traceback" not in wrong_year, wrong_year)
        export_out = outside / f"{label}-both-years"
        chosen_format = "parquet" if parquet else "csv"
        for year, archive in [(2016, "nt2016.zip"), (2021, "nt.zip")]:
            run([*command, "--geo", "POA", "--state", "NT", "--year", str(year),
                 "--zip", archive, "--codes", "800", "--out", str(export_out),
                 "--format", chosen_format], cwd=outside, env=env)
            manifest = json.loads((export_out / f"{year}_GCP_POA_NT_manifest.json").read_text())
            require(manifest["census_year"] == year and manifest["manifest_schema_version"] == 1
                    and manifest["tables"]["G01"]["columns"][0] == f"POA_CODE_{year}"
                    and manifest["tables"]["G01"]["data_years"] == [year]
                    and manifest["writer_version"] == version
                    and manifest["tables"]["G01"]["writer_version"] == version,
                    "Installed export lost year-specific identity")
    read_2016 = ("import abs_census_etl as m; "
                 "df = m.load_census(year=2016, state='NT', source_zip='nt2016.zip', "
                 "codes=[800]); "
                 "assert df['POA_CODE_2016'].tolist() == ['0800']; "
                 "assert df['Tot_P_P'].tolist() == [90]")
    run([str(python), "-I", "-c", read_2016], cwd=outside, env=env)
    read_sa1 = ("import abs_census_etl as m; "
                "df=m.load_census(year=2016,geo='SA1',source_zip='sa1-2016.zip',codes=['1100701']); "
                "assert df.columns[0]=='SA1_7DIGITCODE_2016'; assert df.iloc[0,1]==42")
    run([str(python), "-I", "-c", read_sa1], cwd=outside, env=env)

    output = outside / label
    run([str(launcher), "--geo", "POA", "--state", "NT", "--zip", "nt.zip",
         "--codes", "0800", "--out", str(output)], cwd=outside, env=env)
    manifest = json.loads((output / "2021_GCP_POA_NT_manifest.json").read_text())
    with (output / manifest["tables"]["G01"]["file"]).open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    require(len(rows) == 1 and rows[0]["POA_CODE_2021"] == "0800", rows)
    if parquet:
        parquet_out = outside / f"{label}-parquet"
        run([str(launcher), "--geo", "POA", "--state", "NT", "--zip", "nt.zip",
             "--codes", "0800", "--format", "parquet", "--out", str(parquet_out)],
            cwd=outside, env=env)
        check = ("import pandas as pd, sys; "
                 "df = pd.read_parquet(sys.argv[1]); "
                 "assert df['POA_CODE_2021'].tolist() == ['0800']; "
                 "assert df['Tot_P_P'].tolist() == [100]")
        run([str(python), "-I", "-c", check,
             str(parquet_out / "2021_GCP_POA_NT_G01_wide.parquet")],
            cwd=outside, env=env)
    else:
        failure = run([str(launcher), "--geo", "POA", "--state", "NT", "--zip", "nt.zip",
                       "--format", "parquet"],
                      cwd=outside, env=env, expected=1)
        require(all(text in failure for text in (
            "Parquet export needs", "parquet extra", "Install or update for managed installs",
            "Setup for pip",
            "Platforms and development for uv", "--format csv",
            "file_format='csv'")) and "Traceback" not in failure, failure)
    print(f"PASS: {label} (imports, launchers, both-year exports, 2016 SA1/POA reads, diagnostics, CSV"
          f"{' and Parquet' if parquet else ' without Parquet engine'})", flush=True)


def main() -> int:
    uv = shutil.which("uv")
    if uv is None:
        print("Packaging checks need uv on PATH; see README.md.", file=sys.stderr)
        return 2
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text())
    version = metadata["project"]["version"]
    uv = [uv]
    with tempfile.TemporaryDirectory(prefix="abs-etl-packaging-") as td:
        scratch = guarded_workspace(Path(td))
        env = isolated_environment(dict(os.environ))
        uv_env = uv_environment(env, scratch / "config")
        check_project_configuration(uv, scratch, uv_env, metadata)
        build_source = scratch / "build-source"
        copy_build_source(ROOT, build_source)
        write_decoys(build_source)
        # Negative control: a manifest grafting everything must leak exactly the
        # decoys, proving the exact sdist list below would catch each of them.
        control = scratch / "control-source"
        shutil.copytree(build_source, control)
        with (control / "MANIFEST.in").open("a") as stream:
            stream.write("graft .\n")
        artifacts = scratch / "artifacts"
        run([*uv, "build", "--python", sys.executable, "--out-dir", str(artifacts),
             "--default-index", "https://pypi.org/simple"],
            cwd=build_source, env=uv_env)
        check_artifacts(artifacts, version)
        expected = expected_sdist(version)
        run([*uv, "build", "--sdist", "--python", sys.executable, "--out-dir",
             str(scratch / "control"), "--default-index", "https://pypi.org/simple"],
            cwd=control, env=uv_env)
        control_sdist, = (scratch / "control").glob("*.tar.gz")
        check_files(sdist_files(control_sdist) - expected,
                    {f"abs_census_etl-{version}/{name}" for name in DECOY_FILES},
                    "control leak")
        real_artifacts = scratch / "checkout-artifacts"
        build_checkout(uv, real_artifacts, uv_env)
        wheel, sdist = check_artifacts(real_artifacts, version)
        with tarfile.open(sdist) as archive:
            archive.extractall(scratch / "source", filter="data")
        source, = (scratch / "source").iterdir()
        for name in ("pyproject.toml", "uv.lock", "MANIFEST.in", "README.md",
                     "CHANGELOG.md", "tests/check_packaging.py", "tests/test_abs_census_etl.py"):
            require((source / name).is_file(), name)
        require((source / "uv.lock").read_bytes() == (ROOT / "uv.lock").read_bytes(),
                "Source archive lock differs from checkout")
        print(f"PASS: exact wheel/source contents; {len(DECOY_FILES)} decoys excluded "
              "and exposed by a graft-everything control", flush=True)
        outside = scratch / "outside checkout"
        outside.mkdir()
        with ZipFile(outside / "nt.zip", "w") as archive:
            archive.writestr("2021 Census GCP Postal Areas for NT/2021Census_G01_NT_POA.csv",
                             "POA_CODE_2021,Tot_P_P\n0800,100\n0810,400\n")
        with ZipFile(outside / "nt2016.zip", "w") as archive:
            archive.writestr("2016 Census GCP Postal Areas for NT/2016Census_G01_NT_POA.csv",
                             "POA_CODE_2016,Tot_P_P\n0800,90\n0810,390\n")
        with ZipFile(outside / "sa1-2016.zip", "w") as archive:
            archive.writestr("2016 Census GCP Statistical Area 1 for NSW/2016Census_G01_NSW_SA1.csv",
                             "SA1_7DIGITCODE_2016,Tot_P_P\n1100701,42\n")
        for parquet in (False, True):
            label = "parquet-extra" if parquet else "default"
            environment = scratch / f"env-{label}"
            sync_env = dict(uv_env, UV_PROJECT_ENVIRONMENT=str(environment))
            extra = ["--extra", "parquet"] if parquet else []
            run([*uv, "sync", "--locked", "--no-editable", "--python", sys.executable,
                 "--default-index", "https://pypi.org/simple", *extra], cwd=source, env=sync_env)
            smoke(environment, outside, env, version, parquet=parquet, label=f"source-{label}")
            if parquet:
                scripts = environment / ("Scripts" if os.name == "nt" else "bin")
                python = scripts / ("python.exe" if os.name == "nt" else "python")
                run([str(python), "-m", "unittest", "discover", "-s", "tests", "-q"],
                    cwd=source, env=env)
                print("PASS: offline unit suite from extracted real-checkout sdist (with Parquet)",
                      flush=True)
            # Independent empty environment: exercise the wheel's Requires-Dist
            # and extra metadata, without packages preinstalled by uv sync.
            environment = scratch / f"wheel-env-{label}"
            run([*uv, "venv", "--python", sys.executable, str(environment)],
                cwd=outside, env=uv_env)
            scripts = environment / ("Scripts" if os.name == "nt" else "bin")
            python = scripts / ("python.exe" if os.name == "nt" else "python")
            requirement = f"{wheel}[parquet]" if parquet else str(wheel)
            run([*uv, "pip", "install", "--python", str(python),
                 "--default-index", "https://pypi.org/simple", requirement],
                cwd=outside, env=uv_env)
            smoke(environment, outside, env, version, parquet=parquet, label=f"wheel-{label}")
        check_pip_rollback(uv, scratch, uv_env, env, source)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
