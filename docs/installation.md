# Installation, updates and removal

Start with the [README installation
commands](../README.md#install-or-update-on-windows). Always download a fresh installer,
including for updates and rollback.

## Windows

The managed installer currently targets native x64 Windows 10 (1803+)/11 with Windows
PowerShell 5.1 (or PowerShell 7), the Windows .NET Framework, and `tar.exe`. Run it in a
normal, non-administrator PowerShell window. Elevated runs are refused, including admin
SSH sessions, the built-in Administrator account and admin accounts with UAC disabled.
Use a non-administrator account for managed installation. It needs no Python, Git or
environment activation. The managed download commands require a published stable [GitHub
release](release-assets.md); if none exists, use [source Setup](../README.md#setup)
instead.

For installation, updates or rollback, use the [fresh Windows download
command](../README.md#install-or-update-on-windows).

Append options after the closing `}`: `-Parquet` for Parquet, `-Csv` to switch back,
`-Version VERSION` to choose a release, or `-Prefix "$env:LOCALAPPDATA\My ETL" -BinDir
"$env:LOCALAPPDATA\My tools"`. Always repeat a custom `-Prefix`. Format, repository and
bin choices are saved; `-Version` applies only to that run, so repeat it to remain
pinned. For a fork, replace the repository in the download URL and pass `-Repository
OWNER/REPO`. A non-default source is announced before checking releases; reset with
`-Repository Agent-Cognitive-Enterprise/abs-census-etl` explicitly.

The private root defaults to `%LOCALAPPDATA%\abs-census-etl`, with
`bin\abs-census-etl.exe` inside it. Run the printed full path using `&`: `&
"$env:LOCALAPPDATA\abs-census-etl\bin\abs-census-etl.exe" --help`. The installer changes
neither persistent PATH nor execution policy. The Bypass option in the README download
command applies only to
the child PowerShell process. The launcher preserves the working directory, arguments
and application exit code.

Installation verifies the same source/wheel SHA-256 assets and installs only locked
dependency wheels and the verified app wheel. A small native launcher is compiled with
Windows' existing .NET Framework tools; no compiler is downloaded and the Python
application is not built. The private root stores launcher source/compiler-option and
binary SHA256 hashes. Unchanged runs reuse the executable only when its actual bytes
match that private record; version-resource text alone never authorizes reuse. Source or
compiler-option changes rebuild the launcher. Its embedded root keeps the original
spelling, including Unicode and case-sensitive paths. An atomic `active.json` selects
prepared environments, including options. Old running jobs can finish across a launcher
upgrade; stale owned launcher/pending files are cleaned on later runs once unlocked.
Actual upgrades use a rename pair with a brief publication gap; unchanged-source runs
leave the launcher in place. Failed preparation leaves the previous app usable. Healthy
environments are reused for rollback, format switches and bin moves. Exports are never
migrated.

Folders the installer creates, including a custom bin folder and any missing parents,
allow access only to your account and SYSTEM from the moment they exist; launcher files
do too. An existing bin folder must not let other accounts add files, and no folder
above the root or bin folder may let them rename, delete from, re-permission or own it.
Otherwise the installer stops before changing anything. Folders under `%LOCALAPPDATA%`
pass, as do new folders such as `C:\ETL` or `C:\Tools`. On a default Windows
installation, an existing `C:\Tools` made in Explorer does not: it inherits modify
rights for all signed-in users from `C:\`.

Managed paths must be absolute local-drive paths without reparse points; UNC shares,
junctions and symbolic links are refused. Other architectures are not currently
implemented. Roots longer than 100 characters are refused to leave room for package
paths without changing Windows long-path policy. Equivalent path letter case is
accepted; 8.3 short-name aliases are not supported.

Windows HTTPS downloads use the Windows trust store and default system proxy with the
current account's proxy credentials. An explicit `HTTPS_PROXY` accepts URL credentials
or a scheme-less `host:port` / `ip:port` (HTTP is assumed). For PowerShell downloads,
comma-separated `NO_PROXY` host names/suffixes (or `*`) bypass it. Port-qualified
entries, CIDR/IP ranges and `<local>` are unsupported; use explicit host names or IP
addresses. uv applies its own proxy bypass rules to dependency downloads. uv uses
Windows system certificates by default too. Standard proxy variables and `SSL_CERT_FILE`
/ `SSL_CERT_DIR` reach uv; `-UseNetworkEnv` additionally admits `PIP_CERT` and
`UV_SYSTEM_CERTS`. Repeat that option on each run; network credentials are not saved.
Index/build/environment overrides and external uv config remain isolated; alternate
indexes are unsupported. Proxy authentication and TLS-inspection deployments require
local verification; Windows integrated proxy authentication is not claimed for uv.

Managed PCs may block the unsigned launcher compiled locally with the Windows .NET
compiler, or executables in user-writable folders. Ask your IT team about approved
installation methods if policy blocks it; the installer does not bypass AppLocker,
Defender or application-control policy. If a launcher is damaged, choose a fresh
`-BinDir`, or inspect and move the damaged file aside before rerunning. Close running
jobs if Windows refuses a launcher update.

For cleanup, close ETL jobs and check `active.json`: its `slot` names the folder to keep
under `versions/`. Other versions may be removed with a file manager if rollback is no
longer needed. Keep `python/` while retained environments use it. `cache/` is disposable
when no installer is running. Windows releases/removes `.install-lock` when its handle
closes, including a process crash. Child tools and their descendants are tied to the
installer with a Windows Job Object. Incomplete unselected version/work folders may
remain after abrupt termination; remove them only when no installer is running. Invalid
active JSON is repairable by repeating the command; `settings.json` retains fallback
choices. After a `-BinDir` move, remove the old launcher once the new one works. To
uninstall, follow the [Windows uninstall](#windows-uninstall) steps.

## Linux and macOS

`install.sh` installs and updates a private copy of the application. The README
download command requires a published stable GitHub Release containing the
[release assets](release-assets.md). If there is no published release,
use [source Setup](../README.md#setup) to install from source.

Download a fresh installer on **every install or update**, including rollback. This gets
current installer fixes and bootstrap pins. The same command works for first
installation and updates; Python and Git need not be installed.

Use the [fresh Linux/macOS download
command](../README.md#install-or-update-on-linuxmacos).

For options, append them to that same download command, after `install-abs-census-etl`.
For example, append `--parquet` to include Parquet, `--version VERSION` to select a
published version (including rollback), or `--prefix "$HOME/apps/abs-census-etl"
--bin-dir "$HOME/bin"` for custom paths. Always pass a custom `--prefix` again so the
installer can find that root. Do not update by rerunning an old saved copy of
install.sh. For a fork, replace the repository in the download URL with that fork and
append `--repository OWNER/REPO` on first installation. Keep downloading that fork's
fresh installer on updates. Every non-default repository is announced before checking or
downloading releases. If GitHub redirects the latest lookup to a renamed repository, the
installer also names it before downloading. To return to upstream, append `--repository
Agent-Cognitive-Enterprise/abs-census-etl` explicitly. A version selection applies to
that run: after rollback with `--version`, a plain command checks latest again. Repeat
`--version VERSION` to stay pinned.

The chosen format, repository and bin directory are saved in the installation root and
reused unless explicitly overridden. Thus the no-option download command preserves an
existing default-root Parquet install; `--csv` switches it back explicitly. Network
credentials are never written to settings.

The managed installer currently accepts standard Python on native x86-64/ARM64 Linux
**glibc 2.28+** or **macOS 12+**. A new install defaults to CSV, with optional Parquet.
Some older systems can run CSV dependencies through [source Setup](../README.md#setup). Historical
installation checks covered Linux x86-64 and macOS ARM64; Windows uses the PowerShell
installer above. The shell needs curl, tar and sha256sum (Linux) or shasum (macOS). No
sudo, activation or shell-profile changes are needed.

Default files live in `~/.local/share/abs-census-etl`; the launcher is
`~/.local/bin/abs-census-etl`. Use the printed full launcher path if its directory is
not on PATH. Keep exports and caches outside managed folders. The launcher uses the
caller's working directory and arguments. After a `--bin-dir` move, the previous
launcher remains and still uses the same active environment. Once the new launcher
works, remove the old launcher file from its previous bin directory to avoid ambiguous
PATH entries.

Checksums are verified for both source metadata/lock and the application wheel. Only
locked dependency wheels and the verified app wheel are installed; installation never
builds the app or downloads build tools. A prepared environment is smoke-tested before
the active pointer switches atomically. Healthy matching environments are reused for
rollback, format switches, repairs and bin-directory changes. Reuse requires the same
repository, version, format and platform. A fork's same-numbered version is separate.
Failed preparation preserves the previous active environment and user data. An invalid
pointer is repairable by repeating the download command.

Corporate networks: standard proxy variables and `SSL_CERT_FILE` / `SSL_CERT_DIR` are
preserved. Add `--use-network-env` to explicitly allow `PIP_CERT` as a certificate
bundle and `UV_SYSTEM_CERTS` for uv's system trust store. Reapply that option on each
download command; credentials are not saved. Index overrides and user/system uv
configuration are isolated even with this option. Alternative package indexes are not
supported: the release lock uses PyPI URLs. Corporate access supports certificates and
proxies; a transparent proxy can serve those existing locked URLs. For curl, use its
normal proxy and `CURL_CA_BUNDLE` settings. See [uv
certificates](https://docs.astral.sh/uv/concepts/authentication/certificates/) and
[locked syncing](https://docs.astral.sh/uv/concepts/projects/sync/).

An installation lock excludes concurrent runs in one root. SIGKILL/power loss can leave
`.install-lock` or unselected version folders. Inspect its `pid` and confirm no
installer is running before removing a stale lock. Do not remove a live install's lock.
Keep different installations' bin directories separate; the installer refuses a launcher
owned by another root.

To reclaim space, close running ETL jobs and open the installation root in a file
manager. The `active` file names the environment to **keep** under `versions/`; other
release folders may be removed if rollback is no longer needed. Remove orphan
`.install.*` folders only when no installer is running. The `cache/` folder is
disposable when installation is idle. Keep `python/`: retained environments may still
need its interpreters.

To uninstall, follow the [Linux and macOS uninstall](#linux-and-macos-uninstall)
steps. No background updates or automatic pruning are performed.

## Windows uninstall

1. Close ETL jobs and wait for the installer to finish. If you saved data inside
   the application root, move it elsewhere first.
2. In File Explorer's address bar, paste `%LOCALAPPDATA%\abs-census-etl`.
   This opens the default hidden AppData location without browsing through it.
   For a custom `-Prefix`, open that exact folder instead.
3. Confirm the `.abs-census-etl` marker file is inside the folder. The installer
   does not mark it hidden, so no hidden-items setting is needed to see it.
4. Go to the parent folder and delete **only** `abs-census-etl` (or the verified
   custom root). Do not delete `%LOCALAPPDATA%` or its other contents. The
   default `bin\abs-census-etl.exe` goes with the root. If you chose an external
   `-BinDir`, delete only its `abs-census-etl.exe` file as well.

No PATH, execution-policy or registry cleanup is needed for this managed
installer. Exports and caches outside the removed root remain yours.

## Linux and macOS uninstall

1. Close ETL jobs and wait for the installer to finish. Move data out of the
   managed root before removing it if you saved anything there.
2. On macOS, use Finder **Go → Go to Folder** (Command-Shift-G) and enter
   `~/.local/share/abs-census-etl`. Use Command-Shift-Period to show hidden files.
   On Linux, open your home folder, enable **Show Hidden Files** from the file
   manager menu, then open `.local/share/abs-census-etl`. Use your custom
   `--prefix` folder instead if you selected one.
3. Confirm the hidden `.abs-census-etl` file is inside that folder. Go to its
   parent and delete **only** the application folder. Do not delete `.local`
   or `share` itself.
4. Open `~/.local/bin` the same way (or your custom `--bin-dir`) and delete
   **only** the `abs-census-etl` launcher file, leaving other tools intact.

The marker identifies an installer-owned root; it does not identify your data
folders as disposable. Root removal includes retained environments and private
Python, while exports and caches outside the root remain untouched.
