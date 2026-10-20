# ABS Census ETL

Read **2021 Census General Community Profile (GCP)** DataPack ZIPs from the Australian
Bureau of Statistics (ABS). Select geographic areas and variables, discover table
and variable descriptions, join split tables, and export CSV or optional Parquet.
Geography codes stay strings,
preserving leading zeros such as Darwin `0800`.

The additional geographies and discovery commands below are **unreleased source
features**. Published v0.1.0 supports POA and SA1; its installation URLs remain
pinned to that release. Use the current checkout and reinstall it through
[Setup](#setup) to try these changes before a new release.

## Use and responsibility

This project is free software under the [MIT License](LICENSE). It is provided **as
is**, without warranties or guarantees. You are responsible for how you use it, checking
results against the ABS source data, and keeping backups. Verify outputs before relying
on them for important decisions. To the extent permitted by law, the maintainers accept
no liability for errors, data loss or other consequences of using the software.

## Choose an installation

| Your computer | Managed installer support |
| --- | --- |
| Windows | Native x64 Windows 10 (1803+) or 11; normal PowerShell window |
| Linux | Native x86-64/ARM64, glibc 2.28+; curl, tar and sha256sum |
| macOS | Intel or Apple Silicon, macOS 12+; curl, tar and shasum |

Managed installation needs internet access but no preinstalled Python or Git. It
installs privately for your account; no administrator access, shell-profile edits or
environment activation are needed. Support targets are distinct from
[dated native test records](docs/development.md#recorded-native-validation).

**The commands below need a published stable GitHub Release.** If none is available, use
[Setup](#setup) from a source download. Distribution is through GitHub, not PyPI. Review
the downloaded installer if you need to inspect code before running it; checksums verify
release integrity, not independent publisher trust.

## Install or update on Windows

Open a normal **non-administrator PowerShell** window. Paste this command for both first
installation and updates; it downloads a fresh installer every time:

```powershell
& { $installer = Join-Path $env:TEMP ('abs-census-etl-' + [guid]::NewGuid().ToString('N') + '.ps1'); $protocol = [Net.ServicePointManager]::SecurityProtocol; try { [Net.ServicePointManager]::SecurityProtocol = $protocol -bor 3072; Invoke-WebRequest -UseBasicParsing -Uri 'https://github.com/Agent-Cognitive-Enterprise/abs-census-etl/releases/latest/download/install.ps1' -OutFile $installer; & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $installer @args; if ($LASTEXITCODE -ne 0) { throw "Installer failed (exit $LASTEXITCODE)" } } finally { [Net.ServicePointManager]::SecurityProtocol = $protocol; Remove-Item -LiteralPath $installer -Force -ErrorAction SilentlyContinue } }
```

Append `-Parquet` after the closing `}` for optional Parquet support, or `-Csv` to
switch back. Format and bin choices are saved. For a custom installation, append
`-Prefix "$env:LOCALAPPDATA\My ETL"` and repeat that prefix every time. Use `-Version
VERSION` to select a published version, including rollback; repeat that option to stay
pinned, since the next plain command checks latest.

Run the launcher using its full path (or the path printed by the installer):

```powershell
& "$env:LOCALAPPDATA\abs-census-etl\bin\abs-census-etl.exe" --help
```

Windows does not need persistent PATH or execution-policy changes. The Bypass option
above applies only to the installer child process. Managed PCs may block the unsigned
launcher; follow your IT team's policy. Windows ARM64/x86, elevated installs,
network-share roots and roots over 100 characters are refused. See [Windows options,
permissions and removal](docs/installation.md#windows).

## Install or update on Linux/macOS

Run as your normal user. Paste the same command for first installation and updates; it
downloads a fresh installer every time:

```bash
sh -c 'installer=$(mktemp) || exit; trap '\''rm -f "$installer"'\'' 0; curl -fsSL --proto "=https" --proto-redir "=https" -o "$installer" https://github.com/Agent-Cognitive-Enterprise/abs-census-etl/releases/latest/download/install.sh && sh "$installer" "$@"' install-abs-census-etl
```

Append `--parquet` after `install-abs-census-etl` for optional Parquet, or `--csv` to
switch back. Existing format choices are preserved on a plain update. For a custom
installation, append `--prefix "$HOME/apps/abs-census-etl"` and repeat that prefix every
time. `--version VERSION` selects a published version, including rollback; repeat it to
stay pinned.

Run the launcher using its full path (or the path printed by the installer):

```bash
"$HOME/.local/bin/abs-census-etl" --help
```

Keep exports outside managed folders. A failed preparation keeps the previous active
environment; successful updates retain older versions for rollback. See [Linux/macOS
options, network settings and removal](docs/installation.md#linux-and-macos).

## CLI

### First export

Open a terminal in a folder where you want the output. This downloads the NSW
postal-area DataPack on first use and exports population and median age for Sydney
postal area `2000` to CSV, with a JSON manifest.

Windows PowerShell (managed install):

```powershell
& "$env:LOCALAPPDATA\abs-census-etl\bin\abs-census-etl.exe" `
  --geo POA --state NSW --tables G01 G02 --codes 2000 --out abs_out
```

Linux/macOS (managed install):

```bash
"$HOME/.local/bin/abs-census-etl" \
  --geo POA --state NSW --tables G01 G02 --codes 2000 --out abs_out
```

For a source installation, use `.venv/bin/abs-census-etl` (Unix) or
`.venv\Scripts\abs-census-etl.exe` (Windows) from the checkout. Use an absolute launcher
path when working elsewhere. For custom managed paths, substitute the launcher path
printed during installation.

The exports are `2021_GCP_POA_NSW_G01_wide.csv`, `2021_GCP_POA_NSW_G02_wide.csv` and
`2021_GCP_POA_NSW_manifest.json` under `abs_out/`. CSV is the default; Parquet requires
both installation with the Parquet option and `--format parquet` when exporting. Use a
fresh output folder when switching export formats; files in the other format are
refused. The downloaded DataPack is also cached at
`abs_out/raw/2021_GCP_POA_for_NSW_short-header.zip`. A different output folder
has a different cache; reuse the first ZIP to avoid another download. For example,
after installing Parquet support, run this from the same folder as the first export.

Windows PowerShell (managed install):

```powershell
& "$env:LOCALAPPDATA\abs-census-etl\bin\abs-census-etl.exe" `
  --geo POA --state NSW --tables G01 G02 --codes 2000 --format parquet `
  --out abs_parquet --zip abs_out\raw\2021_GCP_POA_for_NSW_short-header.zip
```

Linux/macOS (managed install):

```bash
"$HOME/.local/bin/abs-census-etl" \
  --geo POA --state NSW --tables G01 G02 --codes 2000 --format parquet \
  --out abs_parquet --zip abs_out/raw/2021_GCP_POA_for_NSW_short-header.zip
```

From another folder, pass the absolute ZIP path instead. `--zip` uses that archive
without downloading into the new folder.

To use an existing ABS ZIP without a download, add `--zip "/path/to/data.zip"` and
select the matching `--geo` and `--state`. Use `--help` for all options. For Darwin, use
`--state NT --codes 0800` with the NT pack. Codes such as `0800` remain strings in
output; request only areas present in the selected pack. Empty or partly unmatched
selections raise errors before any export is written.

### Discover tables and variables

From a current source checkout, list tables or find variables by their ABS long
descriptors. On Windows replace `.venv/bin/python` with
`.venv\Scripts\python.exe`:

```bash
.venv/bin/python abs_census_etl.py --geo POA --state NSW --list-tables
.venv/bin/python abs_census_etl.py --geo POA --state NSW --describe --search median_age
```

Add `--zip path/to/DataPack.zip` for offline discovery. It uses the metadata
workbook included in the complete ABS pack, without reading measure rows or
writing exports. `--search` is a case-insensitive literal match, not a regular
expression. Results are tab-separated; zero matches produce just the headings.

Supported 2021 geographies are **POA, SA1, SA2, SA3, SA4, LGA, SED, CED, STE and
SAL**. For example, use `--geo LGA --codes 17200` for Sydney in the NSW LGA pack,
or `--geo SA2 --codes 101021007` for Braidwood. Filters accept prefixed or bare
codes; returned codes retain the source format. Geography/state must match the
pack; this does not convert boundaries or combine Census years. See the
[geography and discovery guide](docs/data-guide.md#supported-geographies-and-discovery).

Unexpected numeric tokens now stop extraction with a member/column/area/value
error. Exports record the source ZIP's SHA-256 per table. Concurrent exports to
one folder are refused with a busy message; retry after the other export finishes.

## Install as a Python module

Use Python **3.14 from your own project's virtual environment**. Pip can
install the published wheel directly from GitHub; no repository copy or Git
installation is needed:

```bash
python -m pip install "https://github.com/Agent-Cognitive-Enterprise/abs-census-etl/releases/download/v0.1.0/abs_census_etl-0.1.0-py3-none-any.whl"
```

For optional Parquet support:

```bash
python -m pip install "abs-census-etl[parquet] @ https://github.com/Agent-Cognitive-Enterprise/abs-census-etl/releases/download/v0.1.0/abs_census_etl-0.1.0-py3-none-any.whl"
```

Here `python` must be that project's environment interpreter; substitute its
full path if the environment is not activated. These commands install the app
and its declared dependencies into that environment. Pip resolves dependency
ranges; it does not apply the release's uv lock. For module updates, use the
URL of the new release; the version above is deliberately pinned.

```python
from abs_census_etl import load_census

df = load_census(geo="POA", state="NSW", table="G01", codes=["2000"])
```

The managed installer is for the standalone CLI. It keeps its own environment
and does not add imports to your other Python projects.

## Setup

For a released version, open the repository's **Releases** page and download
`abs_census_etl-VERSION.tar.gz` from that release's assets, where `VERSION` is the
release number, such as `0.1.0`. Open a terminal in the folder containing the download
(usually `cd ~/Downloads`, which also works in PowerShell), then extract the archive
and enter the folder it creates:

```text
tar -xf abs_census_etl-VERSION.tar.gz
cd abs_census_etl-VERSION
```

Replace `VERSION` in both lines. `tar` is built into Windows 10 (1803+) and 11, macOS
and Linux; Windows 10 File Explorer cannot open `.tar.gz` files. If your browser
already extracted the download, only run the `cd` line. Run the commands below inside
`abs_census_etl-VERSION`. This source archive includes the lock, license,
documentation and ETL tests. Avoid GitHub's automatic source snapshots when you want
the verified application release archive.

If no release exists, **Code → Download ZIP** or a Git clone provides unreleased
source for development; it is not a release. The ZIP extracts to an
`abs-census-etl-main` folder and a clone creates `abs-census-etl`; open the terminal
inside that folder. Python **3.14** is required for source setup. Managed
installers above are alternatives. Historical native
coverage and revision limits are in
[recorded native validation](docs/development.md#recorded-native-validation).

Linux/macOS:

```bash
python3.14 -m venv .venv
.venv/bin/python -m pip install .
# Optional Parquet support (run instead of, or after, the command above):
.venv/bin/python -m pip install ".[parquet]"
```

Windows PowerShell:

```powershell
py -3.14 -m venv .venv
.venv\Scripts\python.exe -m pip install .
# Optional Parquet support (run instead of, or after, the command above):
.venv\Scripts\python.exe -m pip install ".[parquet]"
```

No environment activation is required. These commands install this local project and its
dependencies, making the module and CLI available in that virtual environment from any
working directory. After a `git pull` or replacing the source with a GitHub download
(including an older version), rerun the same install command from that source folder:
`.venv/bin/python -m pip install .` (Unix), or `.venv\Scripts\python.exe -m pip install
.` (Windows). Use `".[parquet]"` in place of `.` if you need Parquet. Pip copies the
code into the environment: until you reinstall, the installed launcher and imports
outside the checkout still use the old copy, while running the source script uses the
updated checkout. The project's `setup.cfg` forces source copies to refresh on every
build, including when an older download has older timestamps. No manual build deletion
is needed. Importing the module does **not** download anything, create files, or execute
CLI code.

For an exact locked development environment managed by uv instead, see
[Platforms and development](#platforms-and-development). Use either these pip
commands or uv for a given `.venv`, not both.

## Use from Python

Use your project environment after the direct module install, or the source
environment above, when writing Python programs. Managed installation is
intended for the CLI; it does not add an importable module to your system Python. Run
your script with that source environment's Python: `.venv/bin/python your_script.py`
(Unix) or `.venv\Scripts\python.exe your_script.py` (Windows).

```python
from abs_census_etl import load_census

df = load_census(
    geo="POA", state="NSW", table="G01", codes=["2000"],
    keep=["Tot_P_P"], cache_dir="abs_cache",
)
print(df)
```

For median age use table `G02`, column `Median_age_persons`. Split tables such as
`G04A`/`G04B` are joined by geography. POAs approximate Australia Post postcodes; counts
may include ABS confidentiality adjustments. See the
[Python API and data guide](docs/data-guide.md) for multiple tables, offline
ZIPs, leading-zero inputs, manifest paths, export recovery and data caveats.

## Updates, troubleshooting and uninstall

Repeat the fresh download command for your platform, with the same custom root if you
chose one. Do not update using an old saved installer. No application `update`
subcommand or background update runs. Source installs repeat the pip command after
updating the source files. For a uv-managed environment, repeat
`uv sync --locked --python 3.14 --extra parquet` from the project folder.

For a managed installation, close jobs and wait for installers to finish.
Before deleting anything, move any exports you kept inside the application root
somewhere else. Default locations are:

| System | Application root | Launcher |
| --- | --- | --- |
| Windows | `%LOCALAPPDATA%\abs-census-etl` | `bin\abs-census-etl.exe` inside that root |
| Linux/macOS | `~/.local/share/abs-census-etl` | `~/.local/bin/abs-census-etl` |

The root contains a file named `.abs-census-etl`, the installer's ownership
marker (a hidden dotfile on Linux/macOS). Check the marker before removing **only that
application folder**, and
remove the launcher file too if it is outside the root. Do not delete the parent
`AppData`, `.local`, `share` or `bin` folders. Custom paths use the locations you
selected, not necessarily this table. Removing the root removes all retained
versions and private Python; data outside it is preserved.

To find hidden folders, paste `%LOCALAPPDATA%\abs-census-etl` in Windows File
Explorer's address bar; on macOS use Finder **Go → Go to Folder** with
`~/.local/share/abs-census-etl`; on Linux enable **Show Hidden Files** in your
home folder, then open `.local/share/abs-census-etl`. For marker visibility and
step-by-step removal see [Windows](docs/installation.md#windows-uninstall) or
[Linux/macOS](docs/installation.md#linux-and-macos-uninstall).

| Problem | What to check |
| --- | --- |
| No release / download fails | Confirm a release exists; use source Setup until one is published |
| Launcher command not found | Run the printed full path; installation does not add PATH entries |
| Parquet engine missing | Repeat install with `-Parquet` / `--parquet`, then export with `--format parquet` |
| Unmatched area code | Check state, geography and actual areas in the pack; preserve leading zeros |
| Export reports another format | Keep that format or choose a fresh output folder |
| Export reports a busy folder | Wait for the other export to finish and retry; do not delete `.abs-census-etl.lock` |
| Export reports a recovery journal | Read the [recovery guidance](docs/data-guide.md#export-recovery); do not delete it just to bypass the check |
| Windows permissions or policy refusal | Use a private profile folder and normal token; follow local IT policy |

## Platforms and development

See [platforms, development and testing](docs/development.md) for locked uv setup, wheel
availability, dependency maintenance and test tooling. Python 3.14 is the application
support range; managed installers use standard builds. Pip resolves dependency ranges;
`uv sync --locked` uses `uv.lock` exactly and removes unselected packages. Choose pip or uv for a
given `.venv`, not both. To install or restore Parquet in a uv environment, run:

```bash
uv sync --locked --python 3.14 --extra parquet
```

This is the uv command referred to by the Parquet engine error message.

## Tests

From a source checkout with the [development
environment](docs/development.md#platforms-and-development):

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python tests/check_mutations.py
```

On Windows, replace `.venv/bin/python` with `.venv\Scripts\python.exe`. These checks use
offline fixtures. The optional genuine-ABS regression check downloads official packs and compares
selected values with linked ABS QuickStats references. Network packaging and
installer smokes are separate; see the
[validation commands](docs/development.md#tests).

## Release checks for maintainers

The [release contract](docs/release-assets.md) describes bundle generation and
manual publication. A dated maintainer handoff is kept in
`docs/release-readiness.md` in the Git checkout only; it is not included in app
archives and must be refreshed before each release. A signed-off implementation does not publish a
release or demonstrate that hosted workflows have run.

## Project development records

`AGENTS.md` and the daily records under `docs/progress/` document the
project's human-directed implementation and independent-review workflow. They
are kept in the Git repository for transparency; they are not installation
instructions and are excluded from the app archives.
