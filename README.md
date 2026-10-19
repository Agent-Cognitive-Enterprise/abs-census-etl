# ABS Census ETL, Python module and CLI

Reads **2021 Census General Community Profile (GCP)** CSVs from official ABS
DataPack ZIP archives at **POA** (ABS postal area) or **SA1** (small
statistical area) level. The module exposes reusable functions and retains a
CLI. It processes table parts such as `G04A` and `G04B` as one table, keyed on
geography, with checks to prevent silent mismatches. Geography codes remain
strings to retain leading zeros.

## Install or update on Windows

The managed installer currently targets native x64 Windows 10 (1803+)/11 with
Windows PowerShell 5.1 (or PowerShell 7), the Windows .NET Framework, and `tar.exe`.
Run it in a normal, non-administrator PowerShell window. Elevated runs are
refused, including admin SSH sessions, the built-in Administrator account and
admin accounts with UAC disabled. Use a non-administrator account for managed
installation. It needs no Python, Git or environment activation.
The commands require a published [GitHub release](docs/release-assets.md);
if none exists, use [Setup](#setup) instead.

Run this in PowerShell. Download a fresh installer every time, including
updates and rollback:

```powershell
& { $installer = Join-Path $env:TEMP ('abs-census-etl-' + [guid]::NewGuid().ToString('N') + '.ps1'); $protocol = [Net.ServicePointManager]::SecurityProtocol; try { [Net.ServicePointManager]::SecurityProtocol = $protocol -bor 3072; Invoke-WebRequest -UseBasicParsing -Uri 'https://github.com/Agent-Cognitive-Enterprise/abs-census-etl/releases/latest/download/install.ps1' -OutFile $installer; & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $installer @args; if ($LASTEXITCODE -ne 0) { throw "Installer failed (exit $LASTEXITCODE)" } } finally { [Net.ServicePointManager]::SecurityProtocol = $protocol; Remove-Item -LiteralPath $installer -Force -ErrorAction SilentlyContinue } }
```

Append options after the closing `}`: `-Parquet` for Parquet, `-Csv` to switch
back, `-Version VERSION` to choose a release, or
`-Prefix "$env:LOCALAPPDATA\My ETL" -BinDir "$env:LOCALAPPDATA\My tools"`.
Always repeat a custom `-Prefix`. Format, repository and bin choices are saved;
`-Version` applies only to that run, so repeat it to remain pinned. For a fork,
replace the repository in the download URL and pass `-Repository OWNER/REPO`.
A non-default source is announced before checking releases; reset with
`-Repository Agent-Cognitive-Enterprise/abs-census-etl` explicitly.

The private root defaults to `%LOCALAPPDATA%\abs-census-etl`, with
`bin\abs-census-etl.exe` inside it. Run the printed full path using `&`:
`& "$env:LOCALAPPDATA\abs-census-etl\bin\abs-census-etl.exe" --help`.
The installer changes neither persistent PATH nor execution policy. The
Bypass option above applies only to the child PowerShell process. The launcher
preserves the working directory, arguments and application exit code.

Installation verifies the same source/wheel SHA-256 assets and installs only
locked dependency wheels and the verified app wheel. A small native launcher
is compiled with Windows' existing .NET Framework tools; no compiler is
downloaded and the Python application is not built. The private root stores
launcher source/compiler-option and binary SHA256 hashes. Unchanged runs reuse
the executable only when its actual bytes match that private record;
version-resource text alone never authorizes reuse. Source or compiler-option
changes rebuild the launcher. Its embedded root keeps the original spelling,
including Unicode and case-sensitive paths. An atomic `active.json` selects
prepared environments, including options. Old running jobs can finish across a
launcher upgrade; stale owned launcher/pending files are cleaned on later runs
once unlocked. Actual upgrades use a rename pair with a brief publication gap;
unchanged-source runs leave the launcher in place.
Failed preparation leaves the previous app usable. Healthy environments are
reused for rollback, format switches and bin moves. Exports are never migrated.

Folders the installer creates, including a custom bin folder and any missing
parents, allow access only to your account and SYSTEM from the moment they
exist; launcher files do too. An existing bin folder must not let other
accounts add files, and no folder above the root or bin folder may let them
rename, delete from, re-permission or own it. Otherwise the installer stops
before changing anything. Folders under `%LOCALAPPDATA%` pass, as do new
folders such as `C:\ETL` or `C:\Tools`. On a default Windows installation, an
existing `C:\Tools` made in Explorer does not: it inherits modify rights for
all signed-in users from `C:\`.

Managed paths must be absolute local-drive paths without reparse points;
UNC shares, junctions and symbolic links are refused. Other architectures are
not currently implemented. Roots longer than 100 characters are refused to
leave room for package paths without changing Windows long-path policy.
Equivalent path letter case is accepted; 8.3 short-name aliases are not supported.

Windows HTTPS downloads use the Windows trust store and default system proxy
with the current account's proxy credentials. An explicit `HTTPS_PROXY` accepts
URL credentials or a scheme-less `host:port` / `ip:port` (HTTP is assumed).
For PowerShell downloads, comma-separated `NO_PROXY` host names/suffixes
(or `*`) bypass it. Port-qualified entries, CIDR/IP ranges and `<local>` are
unsupported; use explicit host names or IP addresses. uv applies its own proxy
bypass rules to dependency downloads.
uv uses Windows system certificates by default too. Standard proxy variables
and `SSL_CERT_FILE` / `SSL_CERT_DIR` reach uv; `-UseNetworkEnv` additionally
admits `PIP_CERT` and `UV_SYSTEM_CERTS`. Repeat that option on each run; network
credentials are not saved. Index/build/environment overrides and
external uv config remain isolated; alternate indexes are unsupported.
Proxy authentication and TLS-inspection deployments require local verification;
Windows integrated proxy authentication is not claimed for uv.

Managed PCs may block the unsigned launcher compiled locally with the Windows
.NET compiler, or executables in user-writable folders. Ask your IT team about
approved installation methods if policy blocks it; the installer does not bypass
AppLocker, Defender or application-control policy. If a launcher is damaged,
choose a fresh `-BinDir`, or inspect and move the damaged file aside before
rerunning. Close running jobs if Windows refuses a launcher update.

For cleanup, close ETL jobs and check `active.json`: its `slot` names the
folder to keep under `versions/`. Other versions may be removed with a file
manager if rollback is no longer needed. Keep `python/` while retained
environments use it. `cache/` is disposable when no installer is running.
Windows releases/removes `.install-lock` when its handle closes, including a
process crash. Child tools and their descendants are tied to the installer with
a Windows Job Object. Incomplete unselected version/work folders may remain
after abrupt termination; remove them only when no installer is running.
Invalid active JSON is repairable by repeating the command; `settings.json`
retains fallback choices.
After a `-BinDir` move, remove the old launcher once the new one works.
To uninstall, close jobs, remove any external launcher, then remove the marked
private root containing `.abs-census-etl`. Data outside that root stays yours.

## Install or update on Linux/macOS

`install.sh` installs and updates a private copy of the application.
The commands below require a published GitHub Release containing the
[release assets](docs/release-assets.md). If there is no published release,
use [Setup](#setup) to install from source.

Download a fresh installer on **every install or update**, including rollback.
This gets current installer fixes and bootstrap pins. The same command works
for first installation and updates; Python and Git need not be installed.

```bash
sh -c 'installer=$(mktemp) || exit; trap '\''rm -f "$installer"'\'' 0; curl -fsSL --proto "=https" --proto-redir "=https" -o "$installer" https://github.com/Agent-Cognitive-Enterprise/abs-census-etl/releases/latest/download/install.sh && sh "$installer" "$@"' install-abs-census-etl
```

For options, append them to that same download command, after
`install-abs-census-etl`. For example, append `--parquet` to include Parquet,
`--version VERSION` to select a published version (including rollback), or
`--prefix "$HOME/apps/abs-census-etl" --bin-dir "$HOME/bin"` for custom paths.
Always pass a custom `--prefix` again so the installer can find that root.
Do not update by rerunning an old saved copy of install.sh. For a fork,
replace the repository in the download URL with that fork and append
`--repository OWNER/REPO` on first installation. Keep downloading that fork's
fresh installer on updates. Every non-default repository is announced before
checking or downloading releases. If GitHub redirects the latest lookup to a
renamed repository, the installer also names it before downloading.
To return to upstream, append
`--repository Agent-Cognitive-Enterprise/abs-census-etl` explicitly.
A version selection applies to that run: after rollback with `--version`, a
plain command checks latest again. Repeat `--version VERSION` to stay pinned.

The chosen format, repository and bin directory are saved in the installation
root and reused unless explicitly overridden. Thus the no-option download
command preserves an existing default-root Parquet install; `--csv` switches
it back explicitly. Network credentials are never written to settings.

The managed installer currently accepts standard Python on native x86-64/ARM64
Linux **glibc 2.28+** or **macOS 12+**. A new install defaults to CSV, with
optional Parquet. Some older systems can run CSV dependencies through the
source workflow below. Native installation checks cover Linux x86-64 and
macOS ARM64; Windows uses the PowerShell installer above. The shell needs
curl, tar and sha256sum (Linux) or shasum (macOS). No sudo, activation or
shell-profile changes are needed.

Default files live in `~/.local/share/abs-census-etl`; the launcher is
`~/.local/bin/abs-census-etl`. Use the printed full launcher path if its
directory is not on PATH. Keep exports and caches outside managed folders.
The launcher uses the caller's working directory and arguments. After a
`--bin-dir` move, the previous launcher remains and still uses the same active
environment. Once the new launcher works, remove the old launcher file from
its previous bin directory to avoid ambiguous PATH entries.

Checksums are verified for both source metadata/lock and the application wheel.
Only locked dependency wheels and the verified app wheel are installed;
installation never builds the app or downloads build tools. A prepared
environment is smoke-tested before the active pointer switches atomically.
Healthy matching environments are reused for rollback, format switches,
repairs and bin-directory changes. Reuse requires the same repository,
version, format and platform. A fork's same-numbered version is separate.
Failed preparation preserves the previous active environment and user data.
An invalid pointer is repairable by repeating the download command.

Corporate networks: standard proxy variables and `SSL_CERT_FILE` /
`SSL_CERT_DIR` are preserved. Add `--use-network-env` to explicitly allow
`PIP_CERT` as a certificate bundle and `UV_SYSTEM_CERTS` for uv's system trust
store. Reapply that option on each download command; credentials are not saved.
Index overrides and user/system uv configuration are isolated even with this
option. Alternative package indexes are not supported: the release lock uses
PyPI URLs. Corporate access supports certificates and proxies; a transparent
proxy can serve those existing locked URLs. For curl, use its normal proxy
and `CURL_CA_BUNDLE` settings.
See [uv certificates](https://docs.astral.sh/uv/concepts/authentication/certificates/)
and [locked syncing](https://docs.astral.sh/uv/concepts/projects/sync/).

An installation lock excludes concurrent runs in one root. SIGKILL/power loss
can leave `.install-lock` or unselected version folders. Inspect its `pid`
and confirm no installer is running before removing a stale lock. Do not
remove a live install's lock. Keep different installations' bin directories
separate; the installer refuses a launcher owned by another root.

To reclaim space, close running ETL jobs and open the installation root in a
file manager. The `active` file names the environment to **keep** under
`versions/`; other release folders may be removed if rollback is no longer
needed. Remove orphan `.install.*` folders only when no installer is running.
The `cache/` folder is disposable when installation is idle. Keep `python/`:
retained environments may still need its interpreters.

To uninstall, close running jobs, remove the printed `abs-census-etl` launcher
from its bin directory, then remove the marked application root (containing
`.abs-census-etl`) with your file manager. Check custom paths first. This
removes all managed versions, tools and Python copies; exports and caches
stored outside that root remain yours. No background updates or automatic
pruning are performed.

## Setup

Download or clone this repository from GitHub, then open a terminal in its
folder. This project is not published on PyPI. Python **3.14** is currently
required; Linux validation uses Python 3.14.7. The managed Linux/macOS
installers above are alternatives. Native smoke coverage includes Linux
x86-64, macOS ARM64 and Windows 11 x64.

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

No environment activation is required. These commands install this local
project and its dependencies, making the module and CLI available in that
virtual environment from any working directory. After a `git pull` or
replacing the source with a GitHub download (including an older version),
rerun the same install command with that environment's Python:
`python -m pip install .`, or `python -m pip install ".[parquet]"` for Parquet.
Pip copies the code
into the environment: until you reinstall, the installed launcher and imports
outside the checkout still use the old copy, while running the source script
uses the updated checkout. The project's `setup.cfg` forces source copies to
refresh on every build, including when an older download has older timestamps.
No manual build deletion is needed. Importing the module does
**not** download anything, create files, or execute CLI code.

For an exact locked development environment managed by uv instead, see
[Platforms and development](#platforms-and-development). Use either these pip
commands or uv for a given `.venv`, not both.

## Use from Python

```python
from abs_census_etl import load_census, load_census_tables, export_census

# One table, directly as a DataFrame. First call downloads and caches its ZIP.
df = load_census(
    geo="POA", state="NSW", table="G01",
    codes=["2000"],
    keep=["Tot_P_P"],
    cache_dir="abs_cache",
)
print(df)

# Median age is in G02, with its actual ABS field name.
median_age = load_census(
    geo="POA", state="NSW", table="G02", codes=["2000"],
    keep=["Median_age_persons"],
)

# Several tables, returned as dict[str, pandas.DataFrame].
data = load_census_tables(
    geo="SA1", state="NSW", tables=["G01", "G04"],
    cache_dir="abs_cache",
)
population = data["G01"]
age = data["G04"]

# Long/tidy output: geographic code, variable, value.
long_df = load_census(geo="POA", state="NSW", table="G01", long=True)

# Produce CSV files and JSON manifest, returning dict[str, pathlib.Path].
paths = export_census(
    geo="POA", state="NSW", tables=["G01", "G04"],
    out="abs_out", file_format="csv",
)
```

**Fully offline** when you already have an ABS DataPack ZIP:

```python
df = load_census(
    geo="SA1", state="NSW", table="G01",
    source_zip="/path/to/2021_GCP_SA1_for_NSW_short-header.zip",
)
```

`source_zip` bypasses network requests. With no `source_zip`, the
corresponding ZIP is downloaded into `cache_dir` on first use and reused
subsequently. The `state` parameter selects the correct DataPack, not a
secondary in-memory filter. For `codes`, use **strings**, particularly for
leading-zero areas such as Darwin `"0800"` in the NT pack. POA filters accept
`"2000"`, `"POA2000"`, case variations, and surrounding whitespace; output
retains the source identifiers. Integer and finite whole-number float POA
inputs are padded to four digits: `800` and `800.0` match `"0800"`, and
`2000.0` matches `"2000"`. Fractional numbers, negative values, infinities,
NaN, booleans, unsupported numeric types such as Decimal, blank values, and
decimal strings such as `"800.0"` are rejected. Drop blank spreadsheet cells
before passing codes; prefer string columns to preserve identifiers. SA1
floats are not coerced. Any unmatched code raises `ValueError`, including a
partly matched request such as `codes=["2000", "2001"]` when only 2000 is
present. An empty `codes=[]` also raises; omit `codes` to select all areas.
Matching stays strict; there is no ignore-missing option. Malformed code
inputs are rejected before any archive access or download; valid-looking
unmatched codes require reading the data. CLI selection errors exit with code
1 before writing exports.

```python
# A real leading-zero postal area: Darwin, Northern Territory.
darwin = load_census(geo="POA", state="NT", table="G01", codes=["0800"])
# The returned ABS identifier is POA0800.
```

The requested `state` must match the CSV members in a local archive too. An
`AUS` request requires national (`AUST`) members; it does not combine state
files. This applies to both POA and SA1. Direct `find_members` and
`combine_table` calls may omit `state` only when the requested table/parts
belong to one state; mixed-state candidates require an explicit state, even
when the geographic codes happen to match.

## CLI

```bash
.venv/bin/abs-census-etl --geo POA --state NSW --tables G01 G02 --out abs_out
.venv/bin/abs-census-etl --geo SA1 --state NSW --tables G01 --format csv
.venv/bin/abs-census-etl --zip data.zip --geo POA --tables G04 --long --format csv
.venv/bin/abs-census-etl --zip data.zip --geo POA --tables G01 --list-columns
```

On Windows, use `.venv\Scripts\abs-census-etl.exe` instead. From another
working directory, use the absolute path to that environment's launcher.
`python -m abs_census_etl` also works with the environment's Python; direct
`python abs_census_etl.py` use from the checkout remains supported.

CSV is the default for both the API and CLI. For Parquet, install the
`parquet` extra (`.[parquet]` in Setup for pip, or `--extra parquet` with uv
in Platforms and development; managed installs rerun install.sh with
`--parquet`), then pass `file_format="parquet"` in Python or `--format parquet`
on the CLI.

**Default format change (unreleased):** CSV replaces the earlier Parquet
default. No numbered release has been published; the project metadata uses an
initial development version. If you previously exported Parquet, either keep
passing `--format parquet` / `file_format="parquet"`, or choose a fresh output
directory for CSV. Export refuses to write when another format exists for the
same requested geography/state/table, in either wide or long form. It checks
all requested tables before accessing the source archive or downloading, and
leaves old files and the manifest intact on a conflict. No exports are
automatically deleted. See [CHANGELOG.md](CHANGELOG.md).

Before source access, export validates code/column selections, optional
Parquet engine availability, output path types, format collisions and
symlinked output files. Scalar/empty `keep` values are invalid; use
`keep=None` for all measures. Unknown column names and unmatched geographic
codes require reading the data. Pyarrow is the recommended and
integration-tested Parquet engine. Pandas may use fastparquet when installed;
preflight admits that fallback, but fastparquet exports have not been
integration-tested here.

Permission preflight checks existing destination files and the nearest
existing output directory with `os.access`. It is best effort: on POSIX,
effective permissions normally apply, while root may bypass mode bits but
still encounters read-only mounts or other OS restrictions. On Windows,
directory checks do not inspect ACLs and report folders as writable; file
checks detect read-only flags, not Excel locks. These checks cannot guarantee
early refusal for every write failure. A regular-file output path or ancestor
raises `NotADirectoryError`. If no existing ancestor can be found before a
self-parent root, export raises `FileNotFoundError` with drive/share guidance
instead of repeatedly walking an unavailable Windows drive or network share.

Symlinked manifest/data destination files are refused explicitly before source
access. Their referents are not modified. An output-directory link that
resolves to a directory remains usable. This prevents atomic replacement from
silently turning a file symlink into a regular file.

The geography/state manifest retains the **latest export entry for each
table**, including tables exported by earlier calls in a different format.
Each entry has its file name, source archive/URL, format, creation time,
shape, rows and columns. The `file` value is relative to the manifest's
directory, so resolve it with `manifest_path.parent / entry["file"]`,
regardless of the working directory. Legacy absolute/cwd-relative export paths
are converted to basenames when the manifest is next updated. The returned
export paths are unchanged. New source-archive provenance is an absolute
resolved path. Retained legacy relative sources keep their original text and
are marked `source_path_kind="legacy_cwd_relative"`; their old working
directory cannot be reconstructed reliably. Absolute labels from earlier
exports are preserved; unlabelled paths are checked using both POSIX and
Windows path syntax, independently of the current operating system. Top-level
source/format/time fields describe the most recent call only; use the
per-table entries for earlier exports. Re-exporting a table replaces its
entry. This is not an inventory of every file: older wide/long variants can
remain on disk without a separate entry, and missing or manually edited files
are not reconciled. Invalid or incompatible existing manifests raise before
source access. Errors explain how to recover: use a fresh output folder, or
remove only the JSON manifest and rerun. Removing it discards the inventory of
earlier exports; it does not delete CSV/Parquet data. The new manifest will
list only tables exported after recovery. Nothing is removed automatically.

All table files and the JSON manifest are written in a private staging
directory under the output folder before any published file is changed. Staged
data and manifest payloads use `.part` suffixes, so recursive
`*.csv`/`*.parquet` searches do not find unpublished data. Existing output
files are backed up before publication. Data files are replaced one at a time;
the manifest is published last. Catchable write/publication errors, including
Ctrl-C, leave or restore the previous files and inventory; a failed first
export removes newly published files. This handles late lock and disk-full
errors that preflight cannot predict.

Data files and JSON are replaced by new filesystem objects, not overwritten in
place. Hard-linked copies and already-open handles can keep the old data;
reopen the published pathname for the new export. New ownership belongs to the
exporting user, and previous ownership, ACLs and extended attributes are not
preserved. Only existing mode bits are copied for data and JSON; a first
manifest takes its mode from a new data file. No process-wide umask change is
made. The JSON write uses a temporary file, flush/fsync, and atomic rename.
Cleanup retries read-only temporary-file deletion when supported and does not
mask the original export error. Undeletable temporary files are ignored.

Recovery uses a manual journal at `.abs-export-*/recovery.json`. It records a
per-file pending name, available backup, and status. After rollback,
`restored` means the original was put back (or a newly created file removed),
`rollback_failed` means manual repair is needed, and `never_published` means
that target was untouched. Consumed backups are removed from the available
map; backups for untouched files do not need restoring.

Any retained journal blocks another export before source access, including a
malformed journal or one left after completed publication/rollback cleanup.
The check covers the whole output folder, so one journal blocks exports for
every geography and state written there. The refusal names each journal's
`state` (`unreadable` if it cannot be parsed). This prevents a later export
from making the saved backups stale. Verify the published data and manifest,
perform any required repairs, then remove or archive the whole recovery
directory outside the output folder. Do not delete the journal merely to
bypass the check. There is no automatic repair/replay.

A `committed` or `rolled_back` journal means no repair was needed, but cleanup
could not delete the journal, for example because another program held it
open. Cleanup deletes the journal before the other staging files, so an
interrupted cleanup leaves at most a folder without a journal. When cleanup
leaves anything behind, a successful export emits a `RuntimeWarning` and a
failed export adds the same text as a note to its error; the CLI prints these
as `WARNING:` lines or after the `ERROR:` line.

If journal updates fail, the error warns that the journal may be stale and
keeps the recovery directory. If SIGKILL or power loss interrupts publication,
the last `publishing` status may be uncertain: inspect pending files, backups,
and published targets before repairing. This is not an atomic snapshot for
concurrent readers/writers, and no power-loss durability guarantee is made. A
crash during staging before the journal exists has not published anything.
Export never opens or deletes files in `.abs-export-*` folders without a
journal, but it warns about them with `RuntimeWarning`, listing each one: they
can hold full-size `.part` payloads. Delete them when no export is running, since an
export still in progress also has such a folder. Extra staging/backup space
is required.

## Platforms and development

Dependency ranges and the initial development version are defined in
`pyproject.toml`. Pip resolves those ranges; it does not read `uv.lock`.
For the exact locked environment, [install uv][uv-install] **0.12.21 or newer**.
The pip Setup above and this uv workflow both use the checkout's `.venv`, so
choose one of them for that environment. Once uv manages `.venv`, update it
with the `uv sync` command below instead of the Setup pip commands. To return
to pip, recreate the environment with `python3.14 -m venv --clear .venv`
(Windows: `py -3.14 -m venv --clear .venv`) and follow Setup.
From the project directory:

```bash
uv sync --locked --python 3.14 --extra parquet
```

`uv sync` manages that checkout's `.venv` exactly: it removes packages not
selected by the lock and command's extras, including unrelated pip-installed
packages. Omitting `--extra parquet` removes pyarrow and skips Parquet tests.
The development command above includes it for full coverage. `uv` installs
the local project in editable mode.
The lock pins all runtime dependencies, including the optional Parquet engine;
CSV installs omit that engine. The current lock selects pandas 3.0.6,
requests 2.34.2 and pyarrow 26.0.0. Build tooling is declared separately in
`pyproject.toml`; it is not pinned by the runtime lock. A lock containing
multiple platforms does not establish native platform support.

The following limits apply to **standard, non-free-threaded CPython 3.14**
binary wheel tags in the
[current lock](uv.lock); they are not native platform test results. A missing
wheel can trigger a source build requiring compilers and system libraries.
That is outside the beginner setup path.

| Platform | CSV dependency wheels | With Parquet |
| --- | --- | --- |
| Windows x86-64 | Available | Available |
| Windows ARM64 | Available | No pyarrow wheel |
| Windows 32-bit | No pandas wheel | No pandas/pyarrow wheels |
| macOS Intel | macOS 10.15+ | macOS 12+ |
| macOS Apple Silicon | macOS 11+ | macOS 12+ |
| Linux x86-64 / ARM64, glibc | glibc 2.27+ | glibc 2.28+ |
| Linux x86-64 / ARM64, musl | musl 1.2+ | musl 1.2+ |

Free-threaded Python 3.14t is not validated; its wheel limits differ. In
particular, locked numpy 3.14t wheels for Intel macOS require macOS 14+, so
the Intel 10.15+ CSV row does not apply to 3.14t.

For example, macOS 11 and Ubuntu 18.04's glibc 2.27 lack the locked Parquet
wheel, although CSV dependency wheels exist. Managed Python may impose
additional OS limits. The managed Linux/macOS installer uses the stricter
limits listed in [Install or update](#install-or-update-on-linuxmacos).
Other Windows architectures, compilation fallbacks and emulation are not
validated. Native installer validation commands are listed below.
The platform list describes wheel availability, not native CI coverage.

To update dependency versions deliberately, maintainers run `uv lock --upgrade`
and repeat the tests below. Ordinary `uv sync --locked` refuses an outdated
lock rather than silently updating it. No `requirements.txt` is needed.

[uv-install]: https://docs.astral.sh/uv/getting-started/installation/

## Tests

The tests generate synthetic ABS-style ZIP archives in temporary directories,
so they run **without internet access** and without downloading full Census
DataPacks.

Linux/macOS (from the project directory):

```bash
.venv/bin/python -m unittest discover -s tests -v
# Additional mutation checks, run against temporary module copies:
.venv/bin/python tests/check_mutations.py
```

Windows PowerShell:

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -v
.venv\Scripts\python.exe tests/check_mutations.py
```

Installer lifecycle tests run offline as part of discovery on POSIX under a
normal user. Windows/root skip those tests. To run them alone:

```bash
.venv/bin/python -m unittest tests.test_installer -v
```

They use temporary homes, staged archives and a trusted local uv double to
exercise saved options, reuse, version checks, environment isolation,
repeat/update/failure/interruption, checksum/archive guards, concurrency,
literal path/argument handling and data preservation. They do not
prove package-manager or native macOS behavior. The actual bootstrap smoke is:

```bash
.venv/bin/python tests/check_installer.py
```

That additional check uses the network, builds temporary test-only release
artifacts and runs the real pinned uv/managed Python installer under a private
HOME. It tests first/repeated installs, updates, failure preservation, offline
CSV leading zeros, optional Parquet and cached rollback. The real resolver
also checks a live GitHub latest redirect (pandas, NumPy or Requests; at
least one must resolve), and bootstrap downloads real upstream uv assets.
Application assets use temporary local releases; the published project's asset
route still needs validation once a release exists. It publishes nothing and
changes no real-user installation, shell profile, system Python or project
environment.
The installer/check files are checkout tooling and separate release assets;
they are not part of the application sdist/wheel.

For native Windows offline checks (development Python, no Pester or network):

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File tests/test_windows_installer.ps1 -Python .venv\Scripts\python.exe
```

Keep `TEMP` inside your profile for Windows checks: test roots live under it,
and the installer refuses folders that other accounts can modify. To also run
the launcher beneath a case-sensitive folder, set `ABS_INSTALLER_CASE_PARENT`
to an existing folder prepared with `fsutil file setCaseSensitiveInfo FOLDER
enable` (elevated prompt); otherwise that probe prints a SKIP line.

For a full Windows smoke, with development Python and uv available:

```powershell
.venv\Scripts\python.exe tests/check_windows_installer.py
```

The latter builds two temporary test releases, bootstraps the actual pinned
uv/Python, checks CSV and Parquet, update/reuse/rollback, wrong installed
version, failed preparation/publication and Windows file locks. Everything
uses temporary roots; no profile, system Python or real installation is changed.
Native test logs, tested file hashes and runner snapshots are local artifacts
under `docs/evidence/`, excluded from Git. Use the commands below to validate
your checkout; local evidence from earlier revisions does not validate later edits.
The Windows host used restricted non-administrator tokens for these runs; a
separate non-administrator account and enterprise proxy/TLS inspection remain
unverified. For installer changes, run the required syntax check too:

```bash
.venv/bin/python tests/check_powershell_syntax.py
```

It fails with setup advice if no parser is installed; it never silently skips.
Install PowerShell using [Microsoft's instructions][pwsh-install],
or set `ABS_ETL_PWSH` to an existing executable. On Unix the check uses only
that variable or `pwsh` on PATH. Neither tests nor installers download or retain
this maintainer parser automatically. Unit discovery
may skip parsing on other Unix machines without it; the required command above
must pass before handing off Windows installer changes.

The PowerShell check can also consume prepared artifacts using
`tests/check_windows_installer.ps1 -ReleaseDir ABSOLUTE_PATH -Version VERSION
-NextVersion NEXT_VERSION`. Windows installer tests are deliberately excluded
from the application sdist; they run from the checkout or release test workspace.

For packaging validation, with uv on PATH:

```bash
.venv/bin/python tests/check_packaging.py
```

On Windows use `.venv\Scripts\python.exe tests/check_packaging.py`.
This additional check may download tools and dependencies from public PyPI.
It isolates user/system uv configuration in temporary config directories and
clears inherited `UV_*`/`PIP_*` settings, while keeping project `[tool.uv]`
settings active. Only uv commands see the redirected config directories.
Every command runs under a work folder whose empty `uv.toml` ends uv's
parent-directory search, so a `uv.toml` planted in a shared folder such as
`/tmp` is ignored. Each run plants a hostile `uv.toml` above that folder to
prove it. A configuration probe verifies that an impossible
required-version is refused and an exclude-newer lock sync works.
Remove generated `build/` and `abs_census_etl.egg-info/` first if present; the
check refuses to delete pre-existing directories. It builds controlled inputs
with nine decoys (including abs_cache/) and a graft-everything negative
control, then builds the **actual checkout** and checks both artifact sets
against exact file lists. Only build/metadata created by the check is removed.
The checkout's unrelated data is not copied into the controlled build tree.
Selected controlled inputs with symlinked files/parents are refused.

The actual checkout's sdist supplies isolated locked source environments and
its wheel installs in independent empty environments, each with and without
Parquet. Probes check imports, CLI help/errors and CSV/Parquet exports outside
the checkout. The full offline unit suite also runs from the extracted sdist
with Parquet installed. A separate pip probe seeds a stale build copy, changes
source with an older timestamp and retains the stale build copy, then verifies
repeating the same pip command installs the changed source without cleanup.
This probe uses the extracted sdist's build settings.

Environments and final artifacts are temporary. The actual-checkout build
creates build/ and project egg-info while running and removes them afterwards;
the user's `.venv` is not synced.
Export probes always use a local ZIP. To check the complete packaging flow
under optimization, run `.venv/bin/python -O tests/check_packaging.py`.
Offline helper tests alone do not establish optimized build/install behavior.
The online negative control proves real decoy leaks are detectable; the
small offline decoy test only checks placeholder disjointness and preservation
of build inputs.

When adding release files, update `MANIFEST.in` and `SOURCE_FILES` in
`tests/check_packaging.py` together. The real-checkout build catches unlisted
files included by the manifest; matching decoys exercise exclusion in the
controlled tree too. Development checks accept private file modes from a
strict local umask. Before publishing, normalize and validate permissions in
a clean release build so archive contents are readable by their recipients.

To build artifacts yourself (without publishing):

```bash
uv build --python 3.14
```

The source archive includes the project definition, lockfile and tests. The
wheel contains the module, CLI entry point and distribution metadata; it does
not embed the dependency lock. Install the source with `uv sync --locked` for
locked dependencies. Choosing a license remains an owner decision before
public release.

Both offline test commands use the project environment. The dedicated POSIX
data-mode mutation is explicitly skipped on platforms without POSIX mode
semantics; skipped probes are not counted as caught. The mutation script
reports missing dependencies as a setup error before trying any mutations.

Covered: strict all/partial unmatched-code errors; POA prefix/case/whitespace
matching and integer/whole-float padding; bare NT leading-zero parsing and
CSV/Parquet round-trips; explicit/inferred state selection; mixed-state
rejection; AUS/AUST naming for POA and SA1; CSV defaults and mixed-format
migration preflight; project User-Agent; cumulative manifest provenance,
legacy backfill, manifest-relative paths and interrupted-write recovery;
read-only and failed temporary-file cleanup that keeps the original error;
warnings and error notes for leftover journals and staging folders;
read-only output refusal before source access; early format conflicts before
source access; metadata/readme archive noise; import side effects; API return
types; POA and SA1 parsing; leading zero codes; column selection and
missing-value conversion; split-table joins and code alignment; numeric
conflicts; invalid table/geography/state; offline `source_zip`; mocked
cached/failed downloads and cleanup; wide and long transforms; CSV export and
manifest; CLI entry points; optional Parquet output. The fixtures use small
subsets of real ABS field names and real geographic identifiers; counts are
synthetic. The NT bare-code fixtures deliberately omit the official POA prefix
to test numeric inference and leading-zero preservation. G04 shared-column
overlap is deliberately synthetic (the official G04A/G04B share only the code
column). The permanent mutation probes are in `tests/check_mutations.py`; the
daily progress record stores run results.

The Parquet test is automatically skipped when `pyarrow` is not installed.
The offline uv configuration-guard test is skipped when `uv` is not on PATH.

## Geography/data caveats

* ABS POAs approximate Australia Post postcodes; they do not perfectly align.
* SA1 is smaller than POA, but most detailed 2021 Census demographic tables do
  **not** go down to Mesh Block level.
* The 2021 Census uses ASGS Edition 3. Use an explicit geographic
  correspondence for other vintages or boundaries.
* Random ABS confidentiality adjustments can affect small-area counts. Do not
  sum rates, medians or percentages as if they were additive.

ABS DataPacks: https://www.abs.gov.au/census/find-census-data/datapacks

[pwsh-install]: https://learn.microsoft.com/powershell/scripting/install/installing-powershell
