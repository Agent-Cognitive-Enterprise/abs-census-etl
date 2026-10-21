# Changelog

## Unreleased

- Support explicit 2016 GCP alongside the unchanged 2021 default: POA, SA1–SA4,
  LGA, SED, CED, STE and SSC in all states/territories and national AUS. SAL remains
  2021-only; SSC/SAL and seven-/eleven-digit SA1 identifiers are never translated.
- Load, discover and export CSV/Parquet in both years, preserving source header
  names, with separate year-qualified caches, files, manifests and format checks.
  Cooperating exports for either year share the same output-directory lock.
- Add manifest schema version 1 and optional per-table boundary/revision facts.
  Preserve existing 2021 identity fields and real 0.2.0 writer compatibility;
  derive known legacy identity from validated year/geography while keeping
  unknown revisions and hashes separate from latest-call summaries. Use Main
  Structure/Non-ABS Structure labels and administrative type for state borders.
  Retain document text once by SHA-256; oversized/undecodable advisory documents
  retain status and hash without blocking export. Mark unlabelled encodings as
  inferred, and refuse newer manifest schemas before identity/deletion advice.
- Validate retained document hashes, reference/store shapes and table identity
  before reading the source. Reject unreleased inline-document/old boundary
  formats; regenerate those local exports. Mark missing document text explicitly
  and restore availability when the same bytes are supplied again.
- Unreadable advisory documents have null encoding and inference. Offline ABS
  reference checks require reviewed capture hashes pinned in the checker source.
  Copied modules record an unknown writer version when no distribution owns them.
- Set the unreleased source version to 0.3.0.dev0 and record each new manifest's
  and table's writer version; historical writer versions are not invented.
- Geography/state API inputs accept surrounding whitespace. Direct table helpers
  require recognizably single-year GCP archives; mixed/unknown identities that
  earlier versions could filter now fail. Refuse unsupported years/profiles early.
  Unsupported CLI year/geography combinations are argument errors (exit 2).
- Validate download identity before caching. Invalid cache errors name the useful
  path and recovery; rejected downloads identify the source URL instead of a
  deleted temporary path. Destination preflights retain journal, permission,
  manifest, symlink and format protections and orphan warnings on failure.
- Existing output lock files are locked before source access. Without a lock,
  explicit ZIPs receive destination checks before they are opened; invalid source
  identity creates no new lock file, including in an existing empty folder.
- Add year-aware genuine-data checks and a re-runnable real 0.2.0 compatibility
  check, with expanded installed source/wheel and offline regression coverage.

## 0.2.0 — 2026-10-20

### Breaking changes

- Unexpected or nonfinite numeric measure tokens now raise with source context
  instead of becoming missing silently. Only explicit missing markers are accepted.
- Cached and explicit ZIPs receive quick directory and member-header checks that
  refuse encrypted or unsupported-compression members; selected members get CRC
  validation when read. New downloads receive a full CRC scan once. Archive
  errors name the ZIP and recovery steps without CLI tracebacks.
- Exports serialize cooperating writers using a persistent output lock file.
  A busy folder fails before source access; unsupported lock errors retain their
  OS cause and recommend a local folder. Existing locks need read access only.
  Missing explicit ZIP paths or invalid ZIP directories leave no fresh output
  folder. Failures requiring data access can leave the coordination file; do not
  delete it while an export may run.

### Added

- Per-table and latest-call source ZIP SHA-256 values in cumulative manifests;
  historical entries without a known digest are explicitly marked unknown.
- Table/variable discovery from official ABS XLSX metadata through
  `list_census_tables`, `describe_census`, `--list-tables` and `--describe`, with
  literal text searches and logical/split-table selection. Lettered profile
  sections are retained separately from logical table IDs and CSV part IDs.
- 2021 GCP support for SA2, SA3, SA4, LGA, SED, CED, STE and SAL alongside POA/SA1.
  Filters accept bare or prefixed codes while preserving source identifiers.
- Optional real-ABS regression checks with independent QuickStats references,
  split-table source comparisons, metadata and hash verification.
- Guidance for export contention, regenerating outputs after interrupted
  publication, and the purpose of repository development records.

## 0.1.0 — 2026-10-20

### Breaking changes

- CSV is the default export format. For Parquet, install an engine and pass
  `file_format="parquet"` or `--format parquet` explicitly.
- Empty/scalar `codes` and `keep` selections are rejected. Any unmatched code
  raises, including partially matched selections; omit selections for all
  data.
- Direct table helpers infer state only when the requested table/parts are
  unambiguous. Mixed-state calls require explicit state selection.
- Manifest `file` values are relative to the manifest directory. Consumers
  must use `manifest_path.parent / entry["file"]`; legacy paths migrate to
  basenames.
- Symlinked manifest/data destination files are refused before source access;
  output-directory links remain supported.
- Export refuses other-format files for the same requested
  geography/state/table (either shape), so switching formats requires a fresh
  directory or retaining the existing format explicitly.
- Top-level manifest source/format/time fields describe only the latest call;
  consumers must read per-table provenance for retained older exports.
- Data and manifest publication replaces file identity. Hard links/open
  handles may retain old contents; ownership, ACLs and extended attributes are
  not preserved. Only mode bits are copied.
- Retained recovery journals block subsequent exports until verified and
  removed or archived outside the output folder.

### Added and corrected behavior

- Beginner install, first-export and update instructions now lead the README;
  detailed installation, data and development guides ship with the source
  archive. The dated release handoff remains checkout-only.
- GitHub Actions validates release bundles and native installer behavior across
  Linux, macOS and Windows. Manual release preparation defaults to tests only;
  explicit publication verifies uploads and refuses existing releases.
- Release builds pin and hash-check their build tool, include checksums for both
  standalone installers, and keep generated build state outside the checkout.
- Licensed under the MIT License. Source archives and wheels include `LICENSE`,
  and release verification requires it to match the checkout.
- README clarifies AS IS availability and users' responsibility to verify results.
- Correct the TrustedInstaller SID so normal Windows drive ownership passes
  installer location checks.
- Windows PowerShell installer uses the same release assets and install/update
  lifecycle. It bootstraps private Python, installs verified wheels, retains
  saved choices, and atomically switches the active selection after validation.
  Verifies cached launcher bytes against hashes saved in the private root,
  includes compiler flags in rebuild identity and preserves literal root spelling.
  New folders and launcher files have owner+SYSTEM permissions, and bin or
  parent folders that other accounts can modify are refused. Upgrades let old
  jobs finish; later runs clean unlocked owned temporary executables. Elevated
  runs are refused; private Python registration is disabled. Windows locks
  release on process exit and child process trees are tied to the installer
  lifetime.
  Installation needs no elevation, PATH edits or preinstalled Python/Git.
  Native smoke coverage now includes Windows 11 x64 and macOS ARM64 alongside
  Linux x86-64; application release publication remains separate work.
- User-local Linux/macOS shell installer for first installation and repeated
  updates from versioned GitHub release assets. It verifies checksums, prepares
  a managed Python environment from locked dependency wheels and a verified
  application wheel, without user-side builds, and smoke-tests before switching
  the active version. Failed preparation retains the working installation; prior
  versions and user data remain. Saved options survive updates and healthy
  environments are reused for rollback and format changes. CSV defaults with
  optional Parquet. Release publication and broader native platform CI remain
  later work.
- Installable single-module project with setuptools metadata and an
  `abs-census-etl` launcher. Source, module and installed launchers share CLI
  errors, warning output and recovery notes. Distribution remains GitHub-only.
- Python 3.14 is the declared support range. Runtime dependencies are defined
  in `pyproject.toml`, with a portable `uv.lock` for repeatable installs and a
  separate optional `parquet` extra. Build tooling is resolved separately.
- POA filters accept bare/prefixed codes, whitespace/case variations, integers
  and finite whole-number floats; output identifiers retain their source form.
  Malformed inputs fail before archive access. Missing selections remain
  strict.
- Local and downloaded tables validate state before combining parts, including
  national AUS/AUST naming for POA and SA1. Repeated numeric measures compare
  by value and missingness rather than inferred dtype.
- Population and median-age examples use actual G01/G02 fields, real NSW 2000
  and NT 0800 identifiers, and a complete project-venv setup.
- Cumulative manifests retain the latest entry per table with source,
  timestamp, format and shape. New source paths are absolute; unresolved
  legacy relative provenance is explicitly marked rather than guessed.
- Export stages all data/JSON and backs up existing destinations before
  publication, publishes the manifest last, and rolls back catchable failures.
  Incomplete rollback retains recovery backups and per-file journal states.
  Restored/failed/untouched targets and remaining backups are recorded, and
  interrupted publication is detected on the next export. Concurrent
  readers/writers, hard process termination and power loss are not an atomic
  multi-file transaction; see README for recovery limits.
- Manifest writes use atomic replacement and preserve existing permission bits
  or copy a new data file's mode. No process-wide umask changes are made;
  ownership and ACL preservation are outside the guarantee.
- Preflight catches known format, engine, selection, path-type and permission
  errors before source access. Windows directory ACLs/file locks and changes
  after preflight still need write-time error handling; root may bypass mode
  bits but does not bypass every OS restriction.
- Offline regression fixtures and permanent mutation probes cover real ABS
  naming, joins, provenance, defaults, permission handling and failure
  recovery.
- Unavailable drive/share roots terminate ancestor lookup with an error.
- Leftover recovery state is reported rather than silent: export emits
  `RuntimeWarning` when cleanup leaves a journal or staging folder after
  success, adds a note to the error after failure, and warns about staging
  folders without a journal. The journal refusal names each journal's state.
  The CLI prints warnings as `WARNING:` lines and error notes after `ERROR:`.
- Staged payloads use `.part` names to avoid unpublished data appearing in
  recursive CSV/Parquet searches. Data permission preservation is covered
  under multiple isolated umasks; publication/recovery have permanent mutation
  probes.
- Legacy source classification understands POSIX and Windows absolute paths
  and preserves existing absolute labels. Pyarrow is the tested recommended
  engine; fastparquet remains an admitted but not integration-tested pandas
  fallback.
