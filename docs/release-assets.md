# GitHub release assets and installer contract

The user approved this release interface, the managed platform floor and the
initial CSV default on 2026-10-19. Release automation must follow this contract.
Implementation review and publication require separate authorization. Windows
uses the same application assets and lifecycle through install.ps1.

## Release identity

The repository is `Agent-Cognitive-Enterprise/abs-census-etl`. A release tag is
`v<version>`, where `<version>` equals `project.version` in pyproject.toml and the
installed distribution metadata. Versions must use canonical public PEP 440 spelling:
lowercase pre-release labels, `.postN` / `.devN`, and normalized numeric components,
without epochs or local version suffixes. For example, `1.0.0rc1` is accepted,
`1.0.0-rc1` and `1.0RC1` are refused. Epochs (`!`) and local suffixes (`+`), including
URL-encoded forms in latest redirects, are refused. Release asset basenames therefore
need neither character. Assets for an existing tag must never be replaced. Publish a new
tag for changes. The initial development version is not a promise that a release exists.

The installer resolves GitHub's `/releases/latest` redirect once to a concrete tag. It
then downloads all application assets from that exact tag, avoiding mixed releases if
latest changes during installation. `--version VERSION` selects a concrete version
instead. An explicit `--repository OWNER/REPO` supports forks without changing the asset
layout.

## Required assets

| Asset | Contents |
| --- | --- |
| `abs_census_etl-<version>.tar.gz` | Setuptools source distribution |
| `abs_census_etl-<version>-py3-none-any.whl` | Built application wheel |
| `SHA256SUMS` | SHA-256 digests and asset basenames |
| `install.sh` | Standalone POSIX shell installer |
| `install.ps1` | Standalone Windows PowerShell installer |

SHA256SUMS uses the conventional `64-lowercase-hex-digits`, two spaces, `basename`
format, one entry per asset. Lines use LF only and end with a newline; both
installers also use LF. .gitattributes preserves these bytes across checkouts.
The installer requires exactly one matching entry for each
source archive and wheel, compares its digest before extraction, and refuses
missing/duplicate/invalid entries. Checksums provide integrity against the selected
release, not independent authentication of a publisher. The bootstrap shell command
trusts the downloaded installer over GitHub HTTPS.

The source archive has exactly one root, `abs_census_etl-<version>/`, and contains the
module, pyproject.toml, uv.lock, setup.cfg, MANIFEST.in, README, CHANGELOG,
LICENSE, the linked user/development documentation and tests.
Its members must be regular files or directories with portable ASCII path components;
absolute paths, traversal and links are refused. Python and source metadata are checked
before dependency installation; installed wheel metadata must match the selected version
before activation. The committed lock must match the project; installation never
regenerates it.

## Installation lifecycle

Installations use a private user-owned application root, defaulting to
`~/.local/share/abs-census-etl`, with a launcher in `~/.local/bin`. `--prefix` and
`--bin-dir` choose absolute alternatives. No shell profile or system Python is changed.
The installer prints the full launcher path.

Version/environment directories are prepared at their permanent paths; virtual
environments are never moved after creation. An atomic `active` regular file names the
selected directory. A managed launcher reads this pointer and runs that environment's
Python module, preserving the caller's working directory and arguments. User
exports/cache remain outside version directories. Old versions are retained, without
automatic pruning.

An installation lock excludes other installer runs. Preparation includes
checksum/extraction validation, locked dependency sync, metadata/import and CLI smoke
tests. Only then is the active pointer replaced. Failed downloads, installs or smoke
tests leave the old pointer unchanged. The launcher may be replaced first with the same
compatible pointer protocol, so it continues to run the old active version if pointer
publication fails. A candidate that might have reached publication is retained rather
than risk deleting the selected environment. Interrupted preparation cleans up its own
candidate where possible; SIGKILL/power loss may leave a lock or unselected directory
for manual inspection. There is no automatic stale-lock deletion or concurrent-install
recovery.

The launcher lives outside the application root when the default bin path is used. It
refuses to overwrite foreign launchers. Root/managed-state symlinks are refused; the
installer itself creates a known env link inside each owned version directory without
moving the virtual environment. Commands pass arguments literally and never evaluate
path values as shell source.

The managed toolchain uses [uv 0.12.21][uv-release] and standard managed CPython 3.14.
The installer embeds official uv archive hashes for its native targets; it downloads no
unversioned bootstrap script. Python lives under the app root, and its previous
installations are retained for old environments. Runtime dependencies must have binary
wheels. `uv sync --locked --no-build --no-install-project` installs only locked
dependencies, followed by `uv pip install --no-deps --no-build` of the checksummed app
wheel. No setuptools or other build tool is resolved or executed on the end user's
machine. Maintainer builds still use the separately declared build requirements.

The approved default is CSV, with explicit --parquet. Managed targets are native
x86-64/ARM64 Linux glibc 2.28+ or macOS 12+. Other combinations fail before
downloads; lower CSV-only platform floors are not supported by this installer.
Windows initially supports native x64 Windows 10 (1803+)/11, explicitly approved by
the user on 2026-10-19; x86/ARM64 are refused without emulation. A wheel tag
is not native CI evidence. Broader native CI coverage remains separate work.

Successful options are persisted in the root. Re-download the installer on every
run to receive bootstrap fixes; custom roots still require --prefix. Healthy
matching environments are reused without downloads for explicit version
selection. Repository identity separates fork caches; a non-default repository,
or a latest redirect to a renamed repository, is announced before downloads.
Invalid pointer contents are treated as no active selection, preserving retained
environments. The same download command selects installation or update; no
application update subcommand is introduced.

## Local validation

`--release-dir /absolute/path --version VERSION` uses staged source/wheel/checksum
assets instead of fetching the application release. Verification still runs. `--uv
/absolute/path/to/uv` explicitly trusts that local tool instead of bootstrapping uv; the
pinned version must match. These options let maintainers exercise the same installer
without publishing test releases.

[uv-release]: https://github.com/astral-sh/uv/releases/tag/0.12.21

## Windows implementation

The Windows implementation currently targets native x64 Windows 10 (1803+)/11,
PowerShell 5.1/7, .NET Framework and OS-supplied tar.exe. This initial x64
policy is approved; x86/ARM64 are refused and no emulation is attempted. The
x64 uv 0.12.21 ZIP SHA-256 is embedded and checked before extracting uv.exe.
The package is still installed from wheels; the OS-supplied .NET compiler
builds only the small stable executable launcher, with no downloaded tools.

The root defaults to %LOCALAPPDATA%\abs-census-etl and uses a user/SYSTEM-only
DACL. Existing ancestor/bin DACLs are not rewritten, but must not let other
accounts replace managed paths (see below). Existing managed directories
must belong to the caller; reparse points and UNC/device paths are refused.
`-Prefix`/`-BinDir` accept literal absolute local-drive paths, including spaces,
Unicode and apostrophes. A foreign executable is refused before downloads.

The launcher is abs-census-etl.exe, identified through its PE description and
root marker. A SHA-256 of the launcher source is embedded in its version resource.
Matching source leaves the executable byte-for-byte unchanged, including on a
case-only prefix repeat. Source changes trigger compilation and publication:
the existing executable is renamed aside, with restoration on failure. Only
actual upgrades have a brief two-rename publication gap. Owned stale temporary
executables are cleaned before source comparison; locked copies wait for a
later run. Scoped names also allow interrupted compiler output to be removed;
unattributable generic legacy files are left untouched.
Running readers that deny rename cause a bounded retry then a clear failure.
Damaged/foreign launchers require a fresh bin directory or manual inspection.
Its native argument forwarding quotes for Windows' argv rules; no cmd.exe,
PowerShell expression evaluation or application update subcommand is involved.
Future incompatible pointer protocols still require an explicit migration.

Windows active.json atomically combines slot/version/format/repository/bin
selection. It is authoritative over fallback settings.json, so interruption
between the two writes does not lose the selected options. Publication uses
same-directory .NET File.Replace/File.Move. A failed activation retains both
the old selection and ready candidate. Locked/read-only files can refuse a
write; old selection bytes remain intact on tested publication failure.
Existing versions and external user data remain untouched. Retained copies
are not an automatic pruning or power-loss recovery journal.

An OpenOrCreate/FileShare.None/DeleteOnClose FileStream lock excludes other
installers; the OS removes it on close, including process termination. Old
closed lock files are reusable. Child commands use interruptible timed waits
and a KILL_ON_JOB_CLOSE Windows Job Object covering descendants; normal
interruption also attempts taskkill /T /F. Abrupt termination may leave an
unselected candidate or workspace, not an active selection to incomplete data.
Existing private DACLs are compared before modification; retained trees are
not rewritten on repeat runs. Elevated installer tokens are refused. Python
installation uses both --no-registry and UV_PYTHON_INSTALL_REGISTRY=0; no PEP514
registration, persistent PATH or execution-policy changes are intended.
The environment and current directory of the calling shell are not changed;
uv-only config/network overrides are scoped to native child processes.
UV_SYSTEM_CERTS defaults to true. Windows proxy/TLS inspection installations
are not native-tested; unsigned local launcher compilation can be blocked by
endpoint policy. Roots over 100 characters are refused conservatively without
changing long-path settings. Short 12-hex slot IDs reduce package path lengths;
existing 32-hex slots remain readable. After activation, optional settings-write
failure is a warning: active.json remains authoritative and the run succeeds.

The same -ReleaseDir/-Version and explicit trusted -Uv testing interface is
available. Native tests run against real release archives/wheels in isolated
roots; no application release is published by these checks.

Windows launcher reuse requires SHA256 source/compiler-option and binary hashes
saved in `launcher.json` inside the private root. PE version text is descriptive,
not trust evidence. The generated launcher retains the trusted literal root
spelling; case-insensitive comparisons never rewrite its embedded path. Pending
and published launcher files use an owner+SYSTEM-only protected DACL, including
for custom bin directories. Missing or mismatched private hashes, or a launcher
owned by another account, cause a rebuild rather than a permission repair.

File DACLs cannot stop a principal that may rename a folder on the path. Every
missing root/bin component is therefore created with the private descriptor
already applied (CreateDirectoryW), with no inherited-DACL window. Before any
change, an existing bin directory is refused if an account other than the user,
SYSTEM, Administrators or TrustedInstaller may add entries or write inherited
files (inherit-only grants included). The bin directory and every existing
directory above the root or bin are refused if such an account owns one or may
delete it (except a drive root), delete its children, change its DACL or take
ownership. The root itself is exempt because each run makes it private. Only
allow ACEs are judged; deny ACEs are conservatively not credited.

## Build, validation and manual publication

`CI` runs on main-branch pushes and pull requests with `contents: read` permissions and no
publishing job. The reusable checks build one five-file bundle and pass it to
native jobs on Ubuntu x64/ARM64, macOS ARM64/Intel and Windows x64. Installer
smokes consume that bundle for the current version and build a synthetic next
version to exercise updates/failures. The first [hosted run](https://github.com/Agent-Cognitive-Enterprise/abs-census-etl/actions/runs/39361189657)
passed on ce34be5; each release tag must pass its own run.

From a source checkout with development Python and uv, prepare a local bundle:

```bash
.venv/bin/python scripts/release.py build --tag vVERSION
.venv/bin/python scripts/release.py verify --tag vVERSION
```

Replace `VERSION` with the release's `project.version`; the script refuses a tag
that differs from pyproject.toml or uv.lock. The published 0.2.0 release used
`v0.2.0` on its tagged source. A development checkout such as 0.3.0.dev0 must
first be set to its release version. Output defaults to `dist/release`
and must be absent or empty; `--assets PATH` selects a fresh output folder.
Builds use a temporary copy of the reviewed source allow-list and normalize file
permissions. They leave the checkout's build state and `.venv` alone.
`release-build-constraints.txt` pins setuptools 82.0.1 and its PyPI hashes;
release builds require these hashes. No dependency lock is regenerated.
SHA256SUMS lists the other four assets (it cannot checksum itself).

Before releasing, update the version in pyproject.toml and uv.lock together,
review the dependency/build-tool changes, run the checks, and commit/tag that
reviewed revision. A release tag must already exist, be canonical `vVERSION`,
match source/wheel/lock metadata, and point to a commit on the default branch.
Do not move an existing release tag or replace its assets. Choose a new version.
The project is licensed under the MIT License (`LICENSE`). The source archive
ships it at its root and the wheel ships it as `.dist-info/licenses/LICENSE`,
with `License-Expression: MIT` metadata.

In GitHub Actions, select the **default branch** in "Use workflow from", then
enter that existing version tag in the **tag input** and manually run **Release**.
Do not choose the tag as the workflow ref; the default-branch guard would skip
validation. Leave **publish** false to build/test only. Inspect all platform
results and downloaded `release-assets`/`native-logs-*` artifacts. An explicit
manual run with **publish** true repeats the checks and permits publication
only after every job passes. No tag push, PR or ordinary CI run publishes.

The final job alone has `contents: write`; it executes default-branch tooling,
not scripts from the supplied tag. It rechecks the tested tag commit, refuses
an existing release or draft, creates a new draft, uploads all five assets,
checks exact names and polls asset listings for uploaded state and SHA256
digests (up to five attempts, two seconds apart), rechecks the tag, and
then makes the release public. Development/pre-release versions are marked as
prereleases. Latest-release one-liners normally require a stable release.
Publication explicitly sets make_latest: only a stable version strictly newer
than all published stable versions becomes latest. Older patch and prerelease
releases do not change latest. Existing stable tags must follow the canonical
version contract, or publication refuses them for manual inspection.
Failures before the final publish request leave the draft for inspection. If
that final request times out, publication may already have succeeded: inspect
the release before retrying. The publisher never deletes or overwrites assets;
inspect/remove an incomplete draft manually, or use a new version. Only the
publish job is serialized, across all tags, with a fresh version comparison
immediately before publication; test-only runs never wait for it. GitHub keeps
at most one waiting job per group: a newer publish job cancels an older waiting
one, so re-dispatch any publish job shown as cancelled. Other API/UI publishers
are not serialized.
Remote tag protection is a separate repository setting; these scripts cannot
prevent another authorized actor from moving a tag after publication.

All action references use full commit SHAs verified against their upstream
repositories. No `pull_request_target`, `workflow_run`, persistent checkout
credentials or publishing token is used in test jobs. Hosted Windows runners
may be elevated: `scripts/with_standard_user.ps1` runs checks under a restricted
SAFER NORMALUSER token, with a private desktop and kill-on-close process job.
It creates no accounts or persistent policies. This does not replace validation
from a separately provisioned standard-user profile. TEMP stays inside the
profile; optional case-sensitive-directory probes need an explicitly prepared
`ABS_INSTALLER_CASE_PARENT` as described in the README.

Dependency age policy: the owner chose to retain the current lock and document
age review, with no enforced waiting period for this slice. Before release,
review PyPI upload dates and dependency/build-tool diffs, including recent
uploads such as the current certifi pin. If an age gate is adopted later, use a
fixed UTC cutoff, regenerate the lock deliberately and retest all targets.
Neither a waiting period, hashes nor pinning establishes package safety.

Reference: [GitHub runner labels][runners], [workflow least privilege and SHA
pinning][actions-security], [release asset API][asset-api], and [uv build/hash
options][uv-build]. Native logs are generated artifacts, retained by Actions for
14 days; download them for longer review. Local logs belong in ignored
`docs/evidence/`, never in the application archives.

[runners]: https://docs.github.com/en/actions/reference/runners/github-hosted-runners
[actions-security]: https://docs.github.com/en/actions/reference/security/secure-use
[asset-api]: https://docs.github.com/en/rest/releases/assets
[uv-build]: https://docs.astral.sh/uv/reference/cli/#uv-build

### Publication preconditions and validation boundaries

The write-privileged job uses Ubuntu's system Python 3.12 in isolated mode;
it neither runs setup-uv nor bootstraps Python while holding its release token.
Every bundle verification (release build, native smokes and publication)
requires the source archive LICENSE and the wheel's licenses/LICENSE to equal
the trusted checkout's nonempty LICENSE byte for byte, so a missing or changed
license fails in the CI build job rather than after the native matrix. The
packaging allow-list and exact wheel contents include the license file.

Trusted default-branch tooling validates artifacts using its current exact
archive allow-list. If that list has changed since a tag was made, the tag may
pass its own CI and still fail the final trusted verification. Align/review the
contract before dispatching an older tag; the publisher never loads checker
code from an artifact. No slow-check result bypasses final verification.

Smokes first install the current version with the original bundle SHA256SUMS,
unchanged; synthetic update/failure fixtures use separate generated checksums.
GitHub upload responses need not provide a digest; the later asset listing must.
A missing digest after bounded polling retains the draft and reports a failure.
API errors include GitHub's response body; malformed bundles produce CLI errors.

The Windows restricted-token wrapper allows 50 minutes, inside a 60-minute native
job timeout. Each component command retains its own timeout. These budgets are
not measured hosted performance; inspect the first hosted run before tuning.
Current GitHub runner documentation lists Ubuntu ARM64 for public and private
standard runners; Actions permissions and billable minutes still need checking
in the actual repository. Native evidence from this workstation is not a hosted
checkout test. Local evidence paths in progress notes are ignored artifacts,
not links promised to readers of the Git repository.

Before each release, refresh both dated status records for that revision: the
checkout-only handoff at `docs/release-readiness.md` (version, evidence and approval
policy), and the **Recorded native validation** section of `docs/development.md`
(date, native/hosted coverage and limits). The handoff is excluded from application
archives; the development guide ships in them, so a stale section would reach source
downloads. This contract is the release procedure for source downloads. User
documentation is shipped in the sdist so README links work offline;
installer/CI/release scripts remain checkout tooling.
