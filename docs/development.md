# Platforms, development and testing

These instructions are for a source checkout. Managed-install users can use the
[README](../README.md); they do not need uv or these test commands. Installer/release
tooling is available from the Git checkout, not the app wheel or sdist. The sdist does
include the ETL tests and packaging checks.

## Platforms and development

Dependency ranges and the initial development version are defined in `pyproject.toml`.
Pip resolves those ranges; it does not read `uv.lock`. For the exact locked environment,
[install uv][uv-install] **0.12.21 or newer**. The [pip Setup](../README.md#setup) and
this uv workflow both use the checkout's `.venv`, so choose one of them for that
environment. Once uv manages `.venv`, update it with the `uv sync` command below instead
of the Setup pip commands.

To return to pip without deleting the uv-managed `.venv`, create a **different**
environment outside the checkout. From the project folder, choose a destination
that does not already contain an environment you need:

Linux/macOS:

```bash
python3.14 -m venv ../abs-census-etl-pip-venv
../abs-census-etl-pip-venv/bin/python -m pip install ".[parquet]"
```

Windows PowerShell:

```powershell
py -3.14 -m venv ..\abs-census-etl-pip-venv
..\abs-census-etl-pip-venv\Scripts\python.exe -m pip install ".[parquet]"
```

Keep using that environment's interpreter and launcher, and repeat its pip
command after source updates. Omit `[parquet]` if you only need CSV. The existing
`.venv` stays uv-managed; these commands do not convert or clear it.

For uv development in the original `.venv`, from the project directory:

```bash
uv sync --locked --python 3.14 --extra parquet
```

`uv sync` manages that checkout's `.venv` exactly: it removes packages not selected by
the lock and command's extras, including unrelated pip-installed packages. Omitting
`--extra parquet` removes pyarrow and skips Parquet tests. The development command above
includes it for full coverage. `uv` installs the local project in editable mode. The
lock pins all runtime dependencies, including the optional Parquet engine; CSV installs
omit that engine. The current lock selects pandas 3.0.6, requests 2.34.2 and pyarrow
26.0.0. Build tooling is declared separately in `pyproject.toml`; it is not pinned by
the runtime lock. A lock containing multiple platforms does not establish native
platform support.

The following limits apply to **standard, non-free-threaded CPython 3.14** binary wheel
tags in the
[current lock](../uv.lock); they are not native platform test results. A missing
wheel can trigger a source build requiring compilers and system libraries. That is
outside the beginner setup path.

| Platform | CSV dependency wheels | With Parquet |
| --- | --- | --- |
| Windows x86-64 | Available | Available |
| Windows ARM64 | Available | No pyarrow wheel |
| Windows 32-bit | No pandas wheel | No pandas/pyarrow wheels |
| macOS Intel | macOS 10.15+ | macOS 12+ |
| macOS Apple Silicon | macOS 11+ | macOS 12+ |
| Linux x86-64 / ARM64, glibc | glibc 2.27+ | glibc 2.28+ |
| Linux x86-64 / ARM64, musl | musl 1.2+ | musl 1.2+ |

Free-threaded Python 3.14t is not validated; its wheel limits differ. In particular,
locked numpy 3.14t wheels for Intel macOS require macOS 14+, so the Intel 10.15+ CSV row
does not apply to 3.14t.

For example, macOS 11 and Ubuntu 18.04's glibc 2.27 lack the locked Parquet wheel,
although CSV dependency wheels exist. Managed Python may impose additional OS limits.
The managed Linux/macOS installer uses the stricter limits listed in [Install or
update](../README.md#install-or-update-on-linuxmacos). Other Windows architectures,
compilation fallbacks and emulation are not validated. Native installer validation
commands are listed below. The platform list describes wheel availability, not native CI
coverage.

To update dependency versions deliberately, maintainers run `uv lock --upgrade` and
repeat the tests below. Ordinary `uv sync --locked` refuses an outdated lock rather than
silently updating it. No `requirements.txt` is needed.

[uv-install]: https://docs.astral.sh/uv/getting-started/installation/

## Tests

The tests generate synthetic ABS-style ZIP archives in temporary directories, so they
run **without internet access** and without downloading full Census DataPacks.

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

Installer lifecycle tests run offline as part of discovery on POSIX under a normal user.
Windows/root skip those tests. To run them alone:

```bash
.venv/bin/python -m unittest tests.test_installer -v
```

They use temporary homes, staged archives and a trusted local uv double to exercise
saved options, reuse, version checks, environment isolation,
repeat/update/failure/interruption, checksum/archive guards, concurrency, literal
path/argument handling and data preservation. They do not prove package-manager or
native macOS behavior. The actual bootstrap smoke is:

```bash
.venv/bin/python tests/check_installer.py
```

That additional check uses the network, builds temporary test-only release artifacts and
runs the real pinned uv/managed Python installer under a private HOME. It tests
first/repeated installs, updates, failure preservation, offline CSV leading zeros,
optional Parquet and cached rollback. The real resolver also checks a live GitHub latest
redirect (pandas, NumPy or Requests; at least one must resolve), and bootstrap downloads
real upstream uv assets. Application assets use temporary local releases; the published
project's asset route still needs validation once a release exists. It publishes nothing
and changes no real-user installation, shell profile, system Python or project
environment. The installer/check files are checkout tooling and separate release assets;
they are not part of the application sdist/wheel.

For native Windows offline checks (development Python, no Pester or network):

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File tests/test_windows_installer.ps1 -Python .venv\Scripts\python.exe
```

Keep `TEMP` inside your profile for Windows checks: test roots live under it, and the
installer refuses folders that other accounts can modify. To also run the launcher
beneath a case-sensitive folder, set `ABS_INSTALLER_CASE_PARENT` to an existing folder
prepared with `fsutil file setCaseSensitiveInfo FOLDER enable` (elevated prompt);
otherwise that probe prints a SKIP line.

For a full Windows smoke, with development Python and uv available:

```powershell
.venv\Scripts\python.exe tests/check_windows_installer.py
```

The latter builds two temporary test releases, bootstraps the actual pinned uv/Python,
checks CSV and Parquet, update/reuse/rollback, wrong installed version, failed
preparation/publication and Windows file locks. Everything uses temporary roots; no
profile, system Python or real installation is changed. Native test logs, tested file
hashes and runner snapshots are local artifacts under `docs/evidence/`, excluded from
Git. Use the commands below to validate your checkout; local evidence from earlier
revisions does not validate later edits. The Windows host used restricted
non-administrator tokens for these runs; a separate non-administrator account and
enterprise proxy/TLS inspection remain unverified. For installer changes, run the
required syntax check too:

```bash
.venv/bin/python tests/check_powershell_syntax.py
```

It fails with setup advice if no parser is installed; it never silently skips. Install
PowerShell using [Microsoft's instructions][pwsh-install], or set `ABS_ETL_PWSH` to an
existing executable. On Unix the check uses only that variable or `pwsh` on PATH.
Neither tests nor installers download or retain this maintainer parser automatically.
Unit discovery may skip parsing on other Unix machines without it; the required command
above must pass before handing off Windows installer changes.

The PowerShell check can also consume prepared artifacts using
`tests/check_windows_installer.ps1 -ReleaseDir ABSOLUTE_PATH -Version VERSION
-NextVersion NEXT_VERSION`. Windows installer tests are deliberately excluded from the
application sdist; they run from the checkout or release test workspace.

For packaging validation, with uv on PATH:

```bash
.venv/bin/python tests/check_packaging.py
```

On Windows use `.venv\Scripts\python.exe tests/check_packaging.py`. This additional
check may download tools and dependencies from public PyPI. It isolates user/system uv
configuration in temporary config directories and clears inherited `UV_*`/`PIP_*`
settings, while keeping project `[tool.uv]` settings active. Only uv commands see the
redirected config directories. Every command runs under a work folder whose empty
`uv.toml` ends uv's parent-directory search, so a `uv.toml` planted in a shared folder
such as `/tmp` is ignored. Each run plants a hostile `uv.toml` above that folder to
prove it. A configuration probe verifies that an impossible required-version is refused
and an exclude-newer lock sync works. Remove generated `build/` and
`abs_census_etl.egg-info/` first if present; the check refuses to delete pre-existing
directories. It builds controlled inputs with reviewed decoys (including abs_cache/ and
the checkout-only handoff) and a
graft-everything negative control, then builds the **actual checkout** and checks both
artifact sets against exact file lists. Only build/metadata created by the check is
removed. The checkout's unrelated data is not copied into the controlled build tree.
Selected controlled inputs with symlinked files/parents are refused.

The actual checkout's sdist supplies isolated locked source environments and its wheel
installs in independent empty environments, each with and without Parquet. Probes check
imports, CLI help/errors and CSV/Parquet exports outside the checkout. The full offline
unit suite also runs from the extracted sdist with Parquet installed. A separate pip
probe seeds a stale build copy, changes source with an older timestamp and retains the
stale build copy, then verifies repeating the same pip command installs the changed
source without cleanup. This probe uses the extracted sdist's build settings.

Environments and final artifacts are temporary. The actual-checkout build creates build/
and project egg-info while running and removes them afterwards; the user's `.venv` is
not synced. Export probes always use a local ZIP. To check the complete packaging flow
under optimization, run `.venv/bin/python -O tests/check_packaging.py`. Offline helper
tests alone do not establish optimized build/install behavior. The online negative
control proves real decoy leaks are detectable; the small offline decoy test only checks
placeholder disjointness and preservation of build inputs.

When adding release files, update `MANIFEST.in` and `SOURCE_FILES` in
`tests/check_packaging.py` together. The real-checkout build catches unlisted files
included by the manifest; matching decoys exercise exclusion in the controlled tree too.
Development checks accept private file modes from a strict local umask. Before
publishing, normalize and validate permissions in a clean release build so archive
contents are readable by their recipients.

For an exploratory development build (not the hash-constrained release bundle):

```bash
uv build --python 3.14
```

For a release candidate, use the [release build
command](release-assets.md#build-validation-and-manual-publication) instead: it pins and
hash-checks the build tool and verifies the whole bundle. The source archive includes
the project definition, lockfile, linked docs and tests. The wheel contains the module,
CLI entry point and distribution metadata; it does not embed the dependency lock.
Install the source with `uv sync --locked` for locked dependencies. Both archives
include the MIT `LICENSE`.

Both offline test commands use the project environment. The dedicated POSIX data-mode
mutation is explicitly skipped on platforms without POSIX mode semantics; skipped probes
are not counted as caught. The mutation script reports missing dependencies as a setup
error before trying any mutations.

Covered: strict all/partial unmatched-code errors; POA prefix/case/whitespace matching
and integer/whole-float padding; bare NT leading-zero parsing and CSV/Parquet
round-trips; explicit/inferred state selection; mixed-state rejection; AUS/AUST naming
for POA and SA1; CSV defaults and mixed-format migration preflight; project User-Agent;
cumulative manifest provenance, legacy backfill, manifest-relative paths and
interrupted-write recovery; read-only and failed temporary-file cleanup that keeps the
original error; warnings and error notes for leftover journals and staging folders;
read-only output refusal before source access; early format conflicts before source
access; metadata/readme archive noise; import side effects; API return types; POA and
SA1 parsing; leading zero codes; column selection and missing-value conversion;
split-table joins and code alignment; numeric conflicts; invalid table/geography/state;
offline `source_zip`; mocked cached/failed downloads and cleanup; wide and long
transforms; CSV export and manifest; CLI entry points; optional Parquet output. The
fixtures use small subsets of real ABS field names and real geographic identifiers;
counts are synthetic. The NT bare-code fixtures deliberately omit the official POA
prefix to test numeric inference and leading-zero preservation. G04 shared-column
overlap is deliberately synthetic (the official G04A/G04B share only the code column).
The permanent mutation probes are in `tests/check_mutations.py`; the daily progress
record stores run results.

The Parquet test is automatically skipped when `pyarrow` is not installed. The offline
uv configuration-guard test is skipped when `uv` is not on PATH.

[pwsh-install]: https://learn.microsoft.com/powershell/scripting/install/installing-powershell

## Genuine ABS regression check

Ordinary unit tests remain offline. This optional check downloads official 2021
packs (about 40 MB for the default POA NSW/NT, SA2 NSW and LGA NSW cases), checks
selected population/median-age values against independently read ABS QuickStats
references, compares joined G04 cells to raw CSV parts and verifies metadata and
manifest ZIP hashes:

```bash
.venv/bin/python tests/check_abs_data.py --cache-dir abs_cache --report abs_out/abs-check.json \
  --reference-dir abs_out/reference-pages
# Broader, slower validation of every table and every supported geography:
.venv/bin/python tests/check_abs_data.py --cache-dir abs_cache --all-tables --all-geographies
```

On Windows substitute `.venv\Scripts\python.exe`. Omitting `--cache-dir` uses
and removes a temporary cache; exports are always temporary. The JSON report
records source URLs/SHA-256 values, references and tables checked. Every run
uses default all-table discovery and compares descriptions against every CSV
part header; it also exercises the README default search on POA NSW. With
`--reference-dir`, it retrieves all four linked QuickStats pages, verifies their
summary population/median age, and keeps complete HTML plus URL/time/hash
records. This option requires network even when ZIPs are already cached. Reference
values and their dated official URLs are in `tests/check_abs_data.py`. They are
fixed expectations, not values generated by the code being tested. ABS reissues
or reference revisions require investigation rather than blindly updating the
expected values. The optional check is not run by ordinary offline CI and does
not establish every ABS value as correct. Extra geographies use source-fidelity
checks without independently curated demographic reference values.

The additional offline suite covers explicit missing tokens, unexpected numeric
values, source changes during reading, cumulative hashes, damaged caches,
encrypted or unsupported-compression members, real XLSX sheet schemas,
inline/shared rich text, metadata searches, all supported geographies and
process-level export contention/crash release. New tests and this optional
script are included in the source archive's exact allow-list.

## Recorded native validation

Maintainers refresh this dated section before each release; results apply to
the linked commit, not automatically to every later source download.

On **2026-10-20**, [release validation for v0.1.0][hosted-ci] passed the build,
unit/mutation/packaging checks and native installer jobs on:

- Ubuntu 24.04 x86-64 and ARM64;
- macOS 15 ARM64 and Intel;
- Windows Server 2025 x64, including PowerShell syntax, elevated refusal,
  restricted-token unit tests and PowerShell 5.1/7 installer checks.

These are completed results for the preceding release. Version 0.2.0 must pass
its own release workflow on its tagged source; the linked run predates the
additional geographies and discovery APIs. The daily progress record preserves
local/native feature checks and their exact runtime hashes, rather than treating
an older hosted run as evidence for a later revision.

Earlier local runs included Linux x86-64, macOS ARM64 and Windows 11 x64. Both
hosted and local Windows testing used restricted tokens, not a separately
provisioned standard-user profile. Minimum OS releases, proxy/TLS inspection
and enterprise endpoint policies require separate verification. The real
published application-download route is checked only after publication; local
release-directory smokes do not prove it. A wheel/platform table alone is not
native evidence. Actions uploads retain native log artifacts for 14 days.

[hosted-ci]: https://github.com/Agent-Cognitive-Enterprise/abs-census-etl/actions/runs/39364324272
