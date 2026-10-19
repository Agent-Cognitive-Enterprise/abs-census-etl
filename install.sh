#!/bin/sh
# Install/update a private GitHub release. No Python or Git required to start.
set -eu

DEFAULT_REPOSITORY=Agent-Cognitive-Enterprise/abs-census-etl
REPOSITORY=$DEFAULT_REPOSITORY
UV_VERSION=0.12.21
# New roots default to CSV; updates retain the saved format.
DEFAULT_FORMAT=csv
FORMAT=
FORMAT_SET=0
REPOSITORY_SET=0
NETWORK_ENV=0
VERSION=
PREFIX=
BIN_DIR=
RELEASE_DIR=
LOCAL_UV=
work=
candidate=
lock_owned=0
committed=0
launcher_pending=

say() { printf '%s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
usage() {
    cat <<'EOF'
Usage: sh install.sh [--csv | --parquet] [--version VERSION]
                     [--prefix ABSOLUTE_PATH] [--bin-dir ABSOLUTE_PATH]
                     [--repository OWNER/REPO]
                     [--release-dir ABSOLUTE_PATH --version VERSION]
                     [--uv ABSOLUTE_PATH] [--use-network-env]
Download a fresh installer and repeat the command to update. No activation or sudo is needed.
CSV is the initial default; --parquet includes the optional Parquet engine.
Saved format, repository and bin-directory choices survive updates.
Native x86-64/ARM64: Linux glibc 2.28+ or macOS 12+.
EOF
}
while [ "$#" -gt 0 ]; do
    case "$1" in
        --help|-h) usage; exit 0 ;;
        --use-network-env) NETWORK_ENV=1; shift ;;
        --csv) FORMAT=csv; FORMAT_SET=1; shift ;;
        --parquet) FORMAT=parquet; FORMAT_SET=1; shift ;;
        --version|--prefix|--bin-dir|--repository|--release-dir|--uv)
            [ "$#" -ge 2 ] || die "Missing value for $1"
            case "$1" in
                --version) VERSION=$2 ;;
                --prefix) PREFIX=$2 ;;
                --bin-dir) BIN_DIR=$2 ;;
                --repository) REPOSITORY=$2; REPOSITORY_SET=1 ;;
                --release-dir) RELEASE_DIR=$2 ;;
                --uv) LOCAL_UV=$2 ;;
            esac
            shift 2 ;;
        *) die "Unknown option: $1 (use --help)" ;;
    esac
done
valid_version() {
    case "$1" in ''|*[!0-9a-z.]*) return 1 ;; esac
    # Canonical public PEP 440 spelling, without epochs or local versions.
    printf '%s\n' "$1" | awk '
        BEGIN {
            n="(0|[1-9][0-9]*)"
            pattern="^" n "(\\." n ")*" \
                "((a|b|rc)" n ")?(\\.post" n ")?(\\.dev" n ")?$"
        }
        $0 !~ pattern {exit 1}'
}
if [ -n "$VERSION" ]; then valid_version "$VERSION" || die 'Invalid canonical version'; fi
[ -z "$RELEASE_DIR" ] || {
    [ -n "$VERSION" ] || die '--release-dir requires --version'
    case "$RELEASE_DIR" in /*) ;; *) die '--release-dir must be absolute' ;; esac
    [ -d "$RELEASE_DIR" ] || die '--release-dir must be an existing directory'
}
if [ -n "$LOCAL_UV" ]; then
    case "$LOCAL_UV" in /*) ;; *) die '--uv must be an absolute trusted executable path' ;; esac
    [ -f "$LOCAL_UV" ] && [ -x "$LOCAL_UV" ] || die '--uv is not executable'
fi
case "$REPOSITORY" in
    */*) owner=${REPOSITORY%/*}; repo=${REPOSITORY#*/} ;;
    *) die 'Repository must be OWNER/REPO' ;;
esac
for component in "$owner" "$repo"; do
    case "$component" in ''|*[!A-Za-z0-9_.-]*) die 'Invalid repository' ;; esac
done
[ -n "${HOME:-}" ] || die 'HOME is required'
[ "$(id -u)" != 0 ] || die 'Run as your normal user, without sudo.'

# Preflight before downloads or installation-directory creation.
system=$(uname -s)
machine=$(uname -m)
case "$machine" in
    x86_64|amd64) arch=x86_64 ;;
    arm64|aarch64) arch=aarch64 ;;
    *) die "Unsupported architecture: $machine" ;;
esac
case "$system" in
    Linux)
        libc=$(getconf GNU_LIBC_VERSION 2>/dev/null) ||
            die 'This installer requires Linux with glibc; musl is not supported.'
        libc=${libc#glibc }
        major=${libc%%.*}; minor=${libc#*.}; minor=${minor%%.*}
        case "$major:$minor" in *[!0-9:]*|:|:*) die 'Cannot determine glibc version' ;; esac
        { [ "$major" -gt 2 ] ||
          { [ "$major" -eq 2 ] && [ "$minor" -ge 28 ]; }; } ||
            die 'This installer requires glibc 2.28+.'
        target=$arch-unknown-linux-gnu ;;
    Darwin)
        os_version=$(sw_vers -productVersion) || die 'Cannot determine macOS version'
        major=${os_version%%.*}
        case "$major" in ''|*[!0-9]*) die 'Cannot determine macOS version' ;; esac
        [ "$major" -ge 12 ] || die 'This installer requires macOS 12+.'
        target=$arch-apple-darwin ;;
    *) die "Unsupported operating system: $system" ;;
esac
command -v tar >/dev/null 2>&1 || die 'tar is required'
if command -v sha256sum >/dev/null 2>&1; then
    digest() { sha256sum < "$1" | awk '{print $1}'; }
elif command -v shasum >/dev/null 2>&1; then
    digest() { shasum -a 256 < "$1" | awk '{print $1}'; }
else die 'sha256sum or shasum is required'; fi
download() {
    command -v curl >/dev/null 2>&1 || die 'curl is required for downloads'
    curl --fail --silent --show-error --location \
        --proto '=https' --proto-redir '=https' \
        --connect-timeout 15 --max-time 300 --retry 2 --output "$2" "$1" ||
        die "Download failed: $1"
}
# BEGIN release resolver
resolve_latest() {
    command -v curl >/dev/null 2>&1 || die 'curl is required'
    effective=$(curl --fail --silent --show-error --location \
        --proto '=https' --proto-redir '=https' \
        --connect-timeout 15 --max-time 60 --output /dev/null --write-out '%{url_effective}' \
        "https://github.com/$1/releases/latest") || die 'Cannot resolve the latest release'
    case "$effective" in
        https://github.com/*/releases/tag/v*)
            resolved=${effective#https://github.com/}
            release_repo=${resolved%/releases/tag/v*}
            case "$release_repo" in */*) ;; *) die 'Invalid latest repository' ;; esac
            case "$release_repo" in *[!A-Za-z0-9_./-]*) die 'Invalid latest repository' ;; esac
            VERSION=${resolved#"$release_repo/releases/tag/v"}
            ;;
        *) die 'Latest did not resolve to a versioned release tag' ;;
    esac
    valid_version "$VERSION" || die 'Latest resolved to an invalid version'
}
# END release resolver
PREFIX=${PREFIX:-$HOME/.local/share/abs-census-etl}
# Read data, never shell-source saved options. Only a marked, owned root is read.
# Validate raw input before command substitution can discard trailing newlines.
carriage_return=$(printf '\r')
case "$PREFIX" in *'
'*|*"$carriage_return"*) die 'Paths must not contain newlines or carriage returns' ;; esac
while [ "$PREFIX" != / ] && [ "${PREFIX%/}" != "$PREFIX" ]; do
    PREFIX=${PREFIX%/}
done
[ -n "$PREFIX" ] || die 'Refusing a filesystem root as an install directory'
if [ -f "$PREFIX/.abs-census-etl" ] && [ -O "$PREFIX" ]; then
    [ ! -L "$PREFIX/settings" ] || die 'Refusing symlinked installation settings'
    if [ -f "$PREFIX/settings" ]; then
        while IFS= read -r setting; do
            key=${setting%%=*}; value=${setting#*=}
            case "$key" in
                format) [ -n "$FORMAT" ] || FORMAT=$value ;;
                repository) [ "$REPOSITORY_SET" -eq 1 ] || REPOSITORY=$value ;;
                bin_dir) [ -n "$BIN_DIR" ] || BIN_DIR=$value ;;
                *) die 'Invalid installation settings; inspect the settings file' ;;
            esac
        done < "$PREFIX/settings"
    fi
    # The active release is authoritative if a signal interrupted settings save.
    if [ -f "$PREFIX/active" ] && [ ! -L "$PREFIX/active" ]; then
        previous=$(cat "$PREFIX/active")
        case "$previous" in release-*[!A-Za-z0-9._-]*|'') ;;
            release-*)
                prior=$PREFIX/versions/$previous
                [ ! -L "$prior" ] || die 'Refusing a symlinked active version'
                if [ -f "$prior/release" ] && [ "$FORMAT_SET" -eq 0 ]; then
                    previous_format=$(awk '{print $2}' "$prior/release")
                    case "$previous_format" in csv|parquet) FORMAT=$previous_format ;; esac
                fi
                if [ -f "$prior/repository" ] && [ "$REPOSITORY_SET" -eq 0 ]; then
                    REPOSITORY=$(cat "$prior/repository")
                fi ;;
        esac
    fi
fi
FORMAT=${FORMAT:-$DEFAULT_FORMAT}
case "$FORMAT" in csv|parquet) ;; *) die 'Invalid saved format' ;; esac
case "$REPOSITORY" in
    */*) owner=${REPOSITORY%/*}; repo=${REPOSITORY#*/} ;;
    *) die 'Repository must be OWNER/REPO' ;;
esac
for component in "$owner" "$repo"; do
    case "$component" in ''|*[!A-Za-z0-9_.-]*) die 'Invalid repository' ;; esac
done
if [ "$REPOSITORY" != "$DEFAULT_REPOSITORY" ]; then
    say "Using non-default release repository: $REPOSITORY"
    say "To switch sources, pass --repository $DEFAULT_REPOSITORY (or another OWNER/REPO)."
fi
BIN_DIR=${BIN_DIR:-$HOME/.local/bin}
validate_paths() {
    for path in "$PREFIX" "$BIN_DIR"; do
        case "$path" in /*) ;; *) die 'Installation and bin paths must be absolute' ;; esac
        case "$path" in *'
'*|*"$carriage_return"*) die 'Paths must not contain newlines or carriage returns' ;; esac
        case "$path/" in
            */../*|*/./*) die 'Use absolute paths without dot or dot-dot components' ;;
        esac
    done
    [ "$PREFIX" != / ] && [ "$BIN_DIR" != / ] ||
        die 'Refusing a filesystem root as an install/bin directory'
}
trim_path() (
    value=$1
    while [ "$value" != / ] && [ "${value%/}" != "$value" ]; do value=${value%/}; done
    printf '%s' "$value"
)
validate_paths
PREFIX=$(trim_path "$PREFIX")
BIN_DIR=$(trim_path "$BIN_DIR")
validate_paths
check_dir() {
    [ ! -L "$1" ] || die "Refusing a symlinked managed directory: $1"
    if [ -e "$1" ]; then
        [ -d "$1" ] && [ -O "$1" ] && [ -w "$1" ] ||
            die "Directory must be owned and writable by you: $1"
    fi
}
check_dir "$PREFIX"
if [ -d "$PREFIX" ] && [ ! -f "$PREFIX/.abs-census-etl" ]; then
    [ -z "$(ls -A "$PREFIX")" ] || die "Not a managed installation: $PREFIX"
fi
check_dir "$BIN_DIR"
mkdir -p "$PREFIX" "$BIN_DIR"
PREFIX=$(cd "$PREFIX" && pwd -P)
BIN_DIR=$(cd "$BIN_DIR" && pwd -P)
validate_paths
chmod 700 "$PREFIX"
# Shared ancestors/bin retain the caller's defaults; managed contents are private.
umask 077
if [ -L "$PREFIX/.abs-census-etl" ]; then die 'Refusing a symlinked installation marker'; fi
if [ -e "$PREFIX/.abs-census-etl" ]; then
    [ "$(cat "$PREFIX/.abs-census-etl")" = 1 ] || die 'Unsupported installation layout'
else
    printf '1\n' > "$PREFIX/.abs-census-etl"
fi
lock=$PREFIX/.install-lock
mkdir "$lock" 2>/dev/null ||
    die "Another installer or interrupted install holds $lock. Inspect it before retrying."
lock_owned=1
cleanup() {
    result=$?
    trap - 0 HUP INT TERM
    if [ "$committed" -eq 0 ] && [ -n "$candidate" ]; then
        rm -rf "$candidate" || printf 'WARNING: leftover candidate %s\n' "$candidate" >&2
    fi
    if [ -n "$work" ]; then
        rm -rf "$work" || printf 'WARNING: leftover installer work %s\n' "$work" >&2
    fi
    if [ -n "$launcher_pending" ]; then
        rm -f "$launcher_pending" ||
            printf 'WARNING: leftover launcher staging %s\n' "$launcher_pending" >&2
    fi
    if [ "$lock_owned" -eq 1 ]; then
        rm -f "$lock/pid" || printf 'WARNING: leftover lock owner %s\n' "$lock/pid" >&2
        rmdir "$lock" || printf 'WARNING: leftover lock %s\n' "$lock" >&2
    fi
    exit "$result"
}
trap cleanup 0
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
printf '%s\n' "$$" > "$lock/pid"
for directory in versions tools python cache; do
    check_dir "$PREFIX/$directory"
    mkdir -p "$PREFIX/$directory"
done
for file in active settings; do
    [ ! -L "$PREFIX/$file" ] || die "Refusing a symlinked $file file"
    [ ! -e "$PREFIX/$file" ] || [ -f "$PREFIX/$file" ] || die "Not a regular file: $PREFIX/$file"
done
launcher=$BIN_DIR/abs-census-etl
if [ -e "$launcher" ] || [ -L "$launcher" ]; then
    [ ! -L "$launcher" ] && [ -f "$launcher" ] && [ -O "$launcher" ] ||
        die "Refusing foreign launcher: $launcher"
    grep -F -x '# abs-census-etl managed launcher v1' "$launcher" >/dev/null && \
        grep -F -x "# abs-census-etl root: $PREFIX" "$launcher" >/dev/null ||
        die "Launcher belongs to another installation or program: $launcher. Choose --bin-dir."
fi
work=$(mktemp -d "$PREFIX/.install.XXXXXX") || die 'Cannot create installer workspace'
printf '' > "$work/uv.toml"
mkdir -p "$work/config/user/uv" "$work/config/system/uv"
printf '' > "$work/config/user/uv/uv.toml"
printf '' > "$work/config/system/uv/uv.toml"

if [ -z "$VERSION" ]; then
    resolve_latest "$REPOSITORY"
    # A renamed repository is followed, but never silently.
    if [ "$release_repo" != "$REPOSITORY" ]; then
        say "Latest release redirected from $REPOSITORY to $release_repo (v$VERSION)."
    fi
fi

active=
if [ -f "$PREFIX/active" ]; then
    active=$(cat "$PREFIX/active")
    case "$active" in
        ''|*[!A-Za-z0-9._-]*) active= ;;
        release-*) ;;
        *) active= ;;
    esac
    [ -n "$active" ] || say 'Repairing invalid active pointer; retained environments are preserved.'
    [ ! -L "$PREFIX/versions/${active:-unused}" ] || die 'Refusing a symlinked active version'
fi
verify_environment() {
    "$1/env/bin/python" -I - "$VERSION" "$FORMAT" <<'PYTHON'
import importlib.metadata, sys
import abs_census_etl
if importlib.metadata.version('abs-census-etl') != sys.argv[1]:
    raise SystemExit('Installed project version differs from selected release')
if sys.argv[2] == 'parquet':
    import pyarrow
PYTHON
    [ "$?" -eq 0 ] || return 1
    "$1/env/bin/python" -I -m abs_census_etl --help >/dev/null 2>&1
}
# Cached releases are immutable and scoped to their repository, format and target.
reused=
for slot in "$PREFIX"/versions/release-*; do
    [ -d "$slot" ] && [ ! -L "$slot" ] || continue
    [ -f "$slot/release" ] && [ -f "$slot/repository" ] || continue
    [ "$(cat "$slot/release")" = "$VERSION $FORMAT $target" ] || continue
    [ "$(cat "$slot/repository")" = "$REPOSITORY" ] || continue
    if verify_environment "$slot" >/dev/null 2>&1; then reused=$slot; break; fi
done
save_settings() {
    printf 'format=%s\nrepository=%s\nbin_dir=%s\n' \
        "$FORMAT" "$REPOSITORY" "$BIN_DIR" > "$work/settings"
    mv -f "$work/settings" "$PREFIX/settings" ||
        die 'Could not save settings; repeat your explicit options.'
}
if [ -n "$reused" ] && [ "${reused##*/}" = "$active" ] && [ -x "$launcher" ]; then
    save_settings
    say "Already installed: $VERSION ($FORMAT). $launcher"
    exit 0
fi
if [ -z "$reused" ]; then
asset=abs_census_etl-$VERSION.tar.gz
wheel=abs_census_etl-$VERSION-py3-none-any.whl
if [ -n "$RELEASE_DIR" ]; then
    cp "$RELEASE_DIR/SHA256SUMS" "$work/SHA256SUMS" || die 'Local release checksums missing'
else
    base=https://github.com/${release_repo:-$REPOSITORY}/releases/download/v$VERSION
    download "$base/SHA256SUMS" "$work/SHA256SUMS"
fi
verify_asset() {
    asset_name=$1
    expected=$(awk -v name="$asset_name" '
        $2 == name {count++; hash=$1; if (NF != 2) bad=1}
        END {if (count == 1 && !bad) print hash; else exit 1}' "$work/SHA256SUMS") ||
        die 'Release checksum entry missing or duplicated'
    case "$expected" in *[!0-9a-f]*|'') die 'Invalid SHA-256 entry' ;; esac
    [ "${#expected}" -eq 64 ] || die 'Invalid SHA-256 length'
    if [ -n "$RELEASE_DIR" ]; then
        cp "$RELEASE_DIR/$asset_name" "$work/$asset_name" || die 'Local release asset missing'
    else
        download "$base/$asset_name" "$work/$asset_name"
    fi
    [ "$(digest "$work/$asset_name")" = "$expected" ] || die 'Release asset checksum mismatch'
}
verify_asset "$asset"
source_hash=$expected
verify_asset "$wheel"
wheel_hash=$expected

validate_archive() {
    archive=$1
    archive_root=$2
    tar -tzf "$archive" > "$work/members" || die 'Cannot list release archive'
    tar -tvzf "$archive" > "$work/types" || die 'Cannot inspect archive types'
    while IFS= read -r entry; do
        entry=${entry%/}
        case "$entry" in
            "$archive_root"|"$archive_root/"|"$archive_root/"*) ;;
            *) die "Unexpected archive path: $entry" ;;
        esac
        case "/$entry/" in
            */../*|*/./*|*//*|*[!A-Za-z0-9_./-]*) die "Unsafe archive path: $entry" ;;
        esac
    done < "$work/members"
    while IFS= read -r entry; do
        case "$entry" in -*) ;; d*) ;; *) die 'Archive links/special files are refused' ;; esac
    done < "$work/types"
}
validate_archive "$work/$asset" "abs_census_etl-$VERSION"

network_ca=${PIP_CERT:-${SSL_CERT_FILE:-}}
network_system=${UV_SYSTEM_CERTS:-}
run_uv() (
    # uv-only config selectors must not leak into the installed application's environment.
    for variable in $(env | sed -n \
        's/^\(UV_[A-Za-z0-9_]*\)=.*/\1/p; s/^\(PIP_[A-Za-z0-9_]*\)=.*/\1/p'); do
        unset "$variable"
    done
    if [ "$NETWORK_ENV" -eq 1 ]; then
        # Explicit opt-in to network-only settings, never environment/build selectors.
        [ -z "$network_ca" ] || export SSL_CERT_FILE="$network_ca"
        [ -z "$network_system" ] || export UV_SYSTEM_CERTS="$network_system"
    fi
    unset VIRTUAL_ENV PYTHONPATH PYTHONHOME
    XDG_CONFIG_HOME=$work/config/user; XDG_CONFIG_DIRS=$work/config/system
    APPDATA=$work/config/user; PROGRAMDATA=$work/config/system
    UV_PYTHON_INSTALL_DIR=$PREFIX/python; UV_CACHE_DIR=$PREFIX/cache/uv
    UV_PYTHON_INSTALL_BIN=false
    export XDG_CONFIG_HOME XDG_CONFIG_DIRS APPDATA PROGRAMDATA
    export UV_PYTHON_INSTALL_DIR UV_CACHE_DIR UV_PYTHON_INSTALL_BIN
    cd "$uv_cwd"
    "$uv" "$@"
)
uv_cwd=$work
if [ -n "$LOCAL_UV" ]; then
    uv=$LOCAL_UV
else
    uv=$PREFIX/tools/uv-$UV_VERSION
    [ ! -L "$uv" ] || die 'Refusing a symlinked managed uv'
    if [ ! -x "$uv" ]; then
        case "$target" in
            x86_64-unknown-linux-gnu)
                uv_hash=23f02075b652bb1df64178cfae41b5caf160822e720e2663568f3f5d63bc52c0 ;;
            aarch64-unknown-linux-gnu)
                uv_hash=030b69227b40af8c1981b7301793dc66e71ed3c796ea8688209dd268bd91ec51 ;;
            x86_64-apple-darwin)
                uv_hash=2b336763b396ec6afa20c5a8b083538ca7402445b868311979d740a4344c17d8 ;;
            aarch64-apple-darwin)
                uv_hash=b88bda573e566ef9bced66b155fe0408626fbbc053aee1c30ba686f0728c9447 ;;
        esac
        uv_asset=uv-$target
        uv_base=https://github.com/astral-sh/uv/releases/download/$UV_VERSION
        download "$uv_base/$uv_asset.tar.gz" "$work/uv.tar.gz"
        [ "$(digest "$work/uv.tar.gz")" = "$uv_hash" ] || die 'uv bootstrap checksum mismatch'
        validate_archive "$work/uv.tar.gz" "$uv_asset"
        mkdir "$work/bootstrap"
        tar -xzf "$work/uv.tar.gz" -C "$work/bootstrap" || die 'Cannot extract uv'
        cp "$work/bootstrap/$uv_asset/uv" "$work/uv"
        chmod 700 "$work/uv"
        mv -f "$work/uv" "$uv"
    fi
fi
uv_report=$(run_uv --version) || die 'Cannot run the selected uv executable'
case "$uv_report" in
    "uv $UV_VERSION"|"uv $UV_VERSION ("*) ;;
    *) die "Installer requires uv $UV_VERSION" ;;
esac
run_uv python install --no-bin 3.14 || die 'Managed Python installation failed'
python=$(run_uv python find --managed-python --no-python-downloads 3.14) ||
    die 'Managed Python not found'
candidate=$(mktemp -d "$PREFIX/versions/release-XXXXXX") || die 'Cannot allocate version directory'
mkdir "$candidate/source"
printf '' > "$candidate/uv.toml"
tar -xzf "$work/$asset" -C "$candidate/source" --strip-components=1 || die 'Cannot extract source'
"$python" -I - "$candidate/source" "$VERSION" > "$work/packages" <<'PY'
import pathlib, sys, sysconfig, tomllib
root, version = pathlib.Path(sys.argv[1]), sys.argv[2]
if sys.version_info[:2] != (3, 14) or sysconfig.get_config_var('Py_GIL_DISABLED'):
    raise SystemExit('Standard CPython 3.14 is required')
for name in ('abs_census_etl.py', 'pyproject.toml', 'uv.lock', 'setup.cfg'):
    if not (root / name).is_file(): raise SystemExit(f'Missing release input: {name}')
metadata = tomllib.loads((root / 'pyproject.toml').read_text())
if metadata['project']['name'] != 'abs-census-etl' or metadata['project']['version'] != version:
    raise SystemExit('Release tag and project metadata disagree')
if 'required-version' not in metadata.get('tool', {}).get('uv', {}):
    raise SystemExit('Release must define tool.uv.required-version')
lock = tomllib.loads((root / 'uv.lock').read_text())
for package in lock['package']:
    name = package['name']
    if not name or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789-._' for c in name):
        raise SystemExit('Invalid dependency name in lock')
    if name != 'abs-census-etl': print(name)
PY
install_environment() {
    set -- sync --locked --no-install-project --no-dev --managed-python \
        --python "$python" --no-build
    if [ "$FORMAT" = parquet ]; then set -- "$@" --extra parquet; fi
    while IFS= read -r package; do
        set -- "$@" --no-build-package "$package"
    done < "$work/packages"
    uv_cwd=$candidate/source
    # .venv is created at its permanent location inside this candidate's source tree.
    run_uv "$@" || die 'Locked dependency installation failed'
    run_uv pip install --python "$candidate/source/.venv/bin/python" \
        --no-deps --no-build "$work/$wheel" || die 'Verified wheel installation failed'
}
install_environment
# Keep launcher paths short and stable without relocating the built virtual environment.
ln -s source/.venv "$candidate/env" || die 'Cannot link prepared environment'
verify_environment "$candidate" || die 'Installed version/import/CLI smoke failed'
printf '%s %s %s\n' "$VERSION" "$FORMAT" "$target" > "$candidate/release"
printf '%s\n' "$REPOSITORY" > "$candidate/repository"
printf '%s\n' "$source_hash" > "$candidate/source-sha256"
printf '%s\n' "$wheel_hash" > "$candidate/wheel-sha256"
else
    candidate=$reused
    # Cleanup must never delete an existing reusable environment.
    committed=1
    say "Reusing prepared release $VERSION ($FORMAT)."
fi
root_quoted=$(printf '%s' "$PREFIX" | sed "s/'/'\\\\''/g")
{
    printf '#!/bin/sh\n# abs-census-etl managed launcher v1\n# abs-census-etl root: %s\n' "$PREFIX"
    printf "root='%s'\n" "$root_quoted"
    cat <<'EOF'
IFS= read -r active < "$root/active" || {
    printf 'Installation has no active version; rerun the installer\n' >&2
    exit 1
}
case "$active" in release-*) ;; *) printf 'Invalid active version\n' >&2; exit 1 ;; esac
case "$active" in *[!A-Za-z0-9._-]*) printf 'Invalid active version\n' >&2; exit 1 ;; esac
exec "$root/versions/$active/env/bin/python" -I -m abs_census_etl "$@"
EOF
} > "$work/launcher"
chmod 700 "$work/launcher"
# Publish the compatible launcher first. The old active pointer keeps working.
launcher_pending=$(mktemp "$BIN_DIR/.abs-census-etl.XXXXXX") || die 'Cannot stage launcher'
cp "$work/launcher" "$launcher_pending"
chmod 700 "$launcher_pending"
mv -f "$launcher_pending" "$launcher" || die 'Cannot publish launcher'
printf '%s\n' "${candidate##*/}" > "$work/active"
# From this point a signal may arrive after rename. Retain the ready candidate
# even if publication is interrupted; never remove a possibly active version.
committed=1
mv -f "$work/active" "$PREFIX/active" || die 'Cannot activate prepared version'
save_settings
say "Installed $VERSION ($FORMAT): $launcher"
case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *) say "Use that full path, or add $BIN_DIR to your PATH."
       say 'Shell profiles were not changed.' ;;
esac
