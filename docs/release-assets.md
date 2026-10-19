# GitHub release assets and installer contract

The user approved this release interface, the managed platform floor and the
initial CSV default on 2026-10-19. Release automation must follow this contract;
implementation review and publication require separate authorization. Windows
parity is separate work.

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

SHA256SUMS uses the conventional `64-lowercase-hex-digits`, two spaces, `basename`
format, one entry per asset. The installer requires exactly one matching entry for each
source archive and wheel, compares its digest before extraction, and refuses
missing/duplicate/invalid entries. Checksums provide integrity against the selected
release, not independent authentication of a publisher. The bootstrap shell command
trusts the downloaded installer over GitHub HTTPS.

The source archive has exactly one root, `abs_census_etl-<version>/`, and contains the
module, pyproject.toml, uv.lock, setup.cfg, MANIFEST.in, README, CHANGELOG and tests.
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
No Windows fallback policy is selected. A wheel tag is not native CI evidence.
Native macOS and Windows validation remain separate work.

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
