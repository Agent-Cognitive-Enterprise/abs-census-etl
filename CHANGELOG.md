# Changelog

## Unreleased

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
