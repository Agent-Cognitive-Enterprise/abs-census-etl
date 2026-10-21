# Python API, exports and data caveats

For a first CLI export, see the [README](../README.md#first-export). The Python examples
need the app installed in your Python 3.14 environment, using the
[direct module install](../README.md#install-as-a-python-module) or
[source setup](../README.md#setup).

This guide covers the latest published release, 0.2.0, including searchable
metadata and ten supported 2021 geographies. Users of 0.1.0 should update before
using the new APIs. It also covers the unreleased development version 0.3.0.dev0
in this checkout, which adds 2016 GCP loading/discovery/export, year-qualified
manifests and revision/boundary provenance as below. Those additions are not in
the published 0.2.0 wheel, source archive or managed installation.

## Selecting a Census year (unreleased source)

`year` is a keyword on loading, exports, table listing and variable description. It must
be an integer 2016 or 2021; floats, booleans and strings are rejected. The CLI uses
`--year 2016` or `--year 2021`. The default stays **2021** for compatibility.

| Year | Current geography support | Operations |
| --- | --- | --- |
| 2021 (default) | POA, SA1–SA4, LGA, SED, CED, STE, SAL | Load, discover, CSV/Parquet export |
| 2016 (explicit) | POA, SA1–SA4, LGA, SED, CED, STE, SSC | Load, discover, CSV/Parquet export |

For 2016 POA, use the same Python API with an explicit year:

```python
from abs_census_etl import load_census, list_census_tables, describe_census

source = "2016_GCP_POA_for_NSW_short-header.zip"  # your downloaded official ZIP
people = load_census(year=2016, geo="POA", state="NSW", source_zip=source,
                     codes=["2000"], keep=["Tot_P_P"])
tables = list_census_tables(year=2016, geo="POA", state="NSW", source_zip=source)
variables = describe_census(year=2016, geo="POA", state="NSW", source_zip=source,
                            search="median_age")
```

CLI discovery from a Unix source installation (on Windows use the corresponding
`.venv\Scripts\abs-census-etl.exe` launcher):

```sh
.venv/bin/abs-census-etl --geo POA --state NSW --year 2016 \
  --zip 2016_GCP_POA_for_NSW_short-header.zip --describe --search median_age
```

Omit `source_zip` / `--zip` to download the selected year's pack into its own
cache filename. Returned wide and long data retain `POA_CODE_2016` or
`POA_CODE_2021`, including source prefixes or bare leading zeros. Code filters,
column selection, split-table integrity and strict numeric parsing apply to both.
`--list-columns` and `export_census(year=2016)` support the same year/geography
selection. Every listed geography supports NSW, VIC, QLD, SA, WA, TAS, NT, ACT,
OT and national AUS. SAL/2016 and SSC/2021 fail before source access; they are
separate classifications and are never translated. The public `GEOGRAPHIES`, `VALID_GEOS` and `MEMBER_RE` constants retain
their original 2021 meanings; use the APIs for requested-year validation.

Wrong-year archives report the year found and suggest explicit year selection
only when that year supports the requested geography and profile. No automatic
year inference is performed. Mixed-year archives and non-GCP
profiles are refused, even if some requested GCP table members could be selected.
An explicit incompatible ZIP is refused before creating an output folder or
lock, including a new lock in an existing empty folder. Known destination
problems (recovery journals, formats, symlink/file types, permissions and existing
manifest validity) are checked before opening the source: under the existing
lock when the folder has a lock file, otherwise without writing and again under
the new lock. Any unlocked manifest read is discarded; only the locked read
supplies retained export entries. This does not change the names and
manifest fields of 2021 exports. Download identity and ZIP integrity are checked
before a new cache entry is published; a rejected download names the cache path
and source URL, never a temporary file. An incompatible existing cache
names its path and asks you to move it aside, then retry the requested download.

2016 data uses its own geography. An identical-looking code or variable name
across years is not proof that areas or statistical definitions are comparable.
The national 2016 POA pack uses AUS in CSV names; 2021 uses AUST. National
no-usual-address/migratory codes POA9494/POA9797 are in the 2016 OT pack; asking
for them from a NSW pack raises unmatched-code errors. This increment does not
support Time Series Profiles, historical joins or SSC/SAL conversions.

## Census years and geography

This section and its provenance subsection describe development version
0.3.0.dev0; published 0.2.0 supports 2021 only and writes no schema version.
The default remains 2021; a later Census will not silently change it. Use an
explicit `year=2016` / `--year 2016` for the original 2016 GCP. Both vintages
support POA, SA1, SA2, SA3, SA4, LGA, SED, CED and STE; 2016 adds SSC and 2021
adds SAL. SSC and SAL are not interchangeable. The public GEOGRAPHIES,
VALID_GEOS and MEMBER_RE constants keep their original 2021 meanings.

| Geography | 2016 source code column | 2021 source code column |
| --- | --- | --- |
| SA1 | SA1_7DIGITCODE_2016 (7 digits) | SA1_CODE_2021 (11 digits) |
| SA2 | SA2_MAINCODE_2016 | SA2_CODE_2021 |
| SSC / SAL | SSC_CODE_2016 | SAL_CODE_2021 |
| Other supported geographies | GEO_CODE_2016 | GEO_CODE_2021 |

Wide/long DataFrames and CSV/Parquet files retain these source column names and
code strings. An 11-digit SA1 identifier requested in 2016 is unmatched, never
truncated or translated. Case/whitespace normalization and strict partial-match
refusal apply to both years. Even equal-looking codes may identify different
boundaries, and similarly named variables may have changed definitions. Do not
merge years as if identifiers or measures were automatically comparable.

```sh
.venv/bin/abs-census-etl --year 2016 --geo POA --state NSW --tables G01 G04 \
  --codes 2000 2010 --zip 2016_GCP_POA_for_NSW_short-header.zip --out abs_2016
```

This writes `2016_GCP_POA_NSW_G01_wide.csv`, the G04 CSV and the separate
`2016_GCP_POA_NSW_manifest.json`. A 2021 export can use the same output directory;
formats are checked within each vintage and all cooperating writers share its
one output lock. No cross-year combined manifest is created.

### Validation coverage

Full 59-table payload parsing is recorded for 2016 NSW POA only. All 100
geography/state combinations have archive identity/member-selection checks.
Detailed 110-part headers/metadata and G01/G02/G04 source-cell checks cover the
ten NSW geographies plus NT POA. This does not establish full payload coverage
in every state or national AUS. An unexpected non-numeric cell in an untested
table still raises; configured support does not imply exhaustive certification.

### Revision and boundary provenance

New manifests add `manifest_schema_version: 1`; a missing version is legacy 1.
Unsupported versions fail before source loading. Existing 2021 `census_year: 2021`
and `asgs_edition: 3` fields stay unchanged; 2016 uses edition 2. The latest-call
summary and every newly exported table include `census_year`, `profile: GCP`,
`data_years`, `boundary`, `source_revision` and `writer_version`. The version
is read from the source project or metadata owning the installed module; copied
standalone modules without either record `unknown`. Retained entries keep their
writer version; a missing historical writer version remains absent.

`boundary` names the ASGS framework/edition, `main_structure` versus
`non_abs_structure`, boundary type, geography and exact source code column.
SA1–SA4 and STE belong to the Main Structure; the remaining supported geographies
belong to Non-ABS Structures. STE, LGA, SED and CED have type `administrative`;
POA uses `postal_approximation`, SSC/SAL `locality`, and SA1–SA4 `statistical`.
Its `reference_date` is null: an ASGS edition does not establish each
administrative boundary's effective date. Consult the relevant ABS geography
publication before making historical comparisons. The 2016 framework edition
follows the [July 2016 ABS publication (1270.0.55.001)][asgs-2016],
which explicitly identifies this as the second edition; the actual pack
remains identified by its per-table ZIP SHA-256.

`source_revision` records available Summary_of_Changes.txt members as references
with their original SHA-256, member name and encoding. Document text is stored
once in the manifest's `source_documents` map, keyed by that SHA-256. Resolve each
reference through that map; references from retained tables may name different
documents. Existing document text is re-encoded using its recorded encoding and
checked against its SHA-256 key before reuse. A malformed store, a hash mismatch
or a table identity contradicting the manifest refuses export before the source
is opened. Use a fresh output folder or regenerate the inventory as the error
explains. Unreferenced bodies are removed when no table needs them.

UTF-8 BOMs are recognized; ASCII bytes are encoding-invariant. Unmarked UTF-8
and cp1252 fallback interpretations have `encoding_inferred: true`: successfully
decoding bytes does not prove their intended encoding. Documents over 2 MiB or
with undecodable bytes retain their SHA-256 and `oversize`/`undecodable` status,
with null text, encoding and encoding_inferred, without blocking valid data
exports. Missing or empty change wording means unknown. No revision date is guessed from timestamps or the Census
year. A hash identifies bytes; it does not prove an ABS release name. The 2016
change document records the G14 recode and G36 rename when present in the pack.

For legacy manifests, missing year, profile, covered data years and boundary
fields are derived from the validated manifest identity and vintage definition.
Revision facts and missing ZIP hashes stay unknown; they are never filled from
the latest call's summary or another table's revision. Present identity must match.
For boundary objects, identifying keys are framework, edition, geography and
source_code_column. Descriptive labels (structure, boundary_type, reference_date)
are re-derived from the current vintage definition on each export, including
retained tables. Correcting these labels does not invalidate the inventory or
require a schema change. Contradictory identifying keys are still refused before
source access.

Version 0.2.0 can still update new 2021 manifests: it drops new top-level fields
and fields on tables it re-exports, while retaining optional fields of untouched
tables. A later new writer accepts the missing schema version and derives known
identity again. Historical revision references survive for untouched tables,
but their document text may be unavailable because 0.2.0 discarded the store.
Each reference records `text_status`: available, missing, oversize or undecodable.
A missing body has status missing and the enclosing revision is unavailable if
no nonempty text remains. Supplying the same document again restores availability
for all references. References retain the known hash and encoding facts.
A missing document is never replaced by the latest call's different revision.
Keep each table's provenance when combining outputs; older writers cannot enforce
new schema-version rules. In particular, 0.2.0 can erase a future schema marker;
keep future-schema outputs in separate folders rather than using that old writer
on them. This compatibility promise concerns 2021 manifests,
not asking 0.2.0 to process 2016.

Formats produced only during unreleased development (inline document text or the
old statistical/non_ABS structure labels) are refused rather than migrated.
Regenerate those local exports in a fresh folder. Historical evidence remains
unchanged; it is not an inventory for a current export.

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

`source_zip` bypasses network requests. With no `source_zip`, the corresponding ZIP is
downloaded into `cache_dir` on first use and reused subsequently. The `state` parameter
selects the correct DataPack, not a secondary in-memory filter. For `codes`, use
**strings**, particularly for leading-zero areas such as Darwin `"0800"` in the NT pack.
POA filters accept `"2000"`, `"POA2000"`, case variations, and surrounding whitespace;
output retains the source identifiers. Integer and finite whole-number float POA inputs
are padded to four digits: `800` and `800.0` match `"0800"`, and `2000.0` matches
`"2000"`. Fractional numbers, negative values, infinities, NaN, booleans, unsupported
numeric types such as Decimal, blank values, and decimal strings such as `"800.0"` are
rejected. Drop blank spreadsheet cells before passing codes; prefer string columns to
preserve identifiers. Floats for non-POA geographies are not coerced. Any unmatched code raises
`ValueError`, including a partly matched request such as `codes=["2000", "2001"]` when
only 2000 is present. An empty `codes=[]` also raises; omit `codes` to select all areas.
Matching stays strict; there is no ignore-missing option. Malformed code inputs are
rejected before any archive access or download; valid-looking unmatched codes require
reading the data. CLI selection errors exit with code 1 before writing exports.

```python
# A real leading-zero postal area: Darwin, Northern Territory.
darwin = load_census(geo="POA", state="NT", table="G01", codes=["0800"])
# The returned ABS identifier is POA0800.
```

The requested `state` must match the CSV members in a local archive too. An `AUS`
request requires national members (`AUS` in 2016, `AUST` in official 2021 CSV
names); it does not combine state files. This
applies to every supported geography. Direct `find_members` and `combine_table` calls may omit
`state` only when the requested table/parts belong to one state; mixed-state candidates
require an explicit state, even when the geographic codes happen to match.

Logical-table extraction (for example G04) requires every part declared by the
metadata workbook. Missing metadata for a split table is an error. Explicit
part requests such as G04A intentionally select only that part. find_members
lists available filenames; it does not certify extraction completeness.
Duplicate raw CSV headers, including whitespace variants, are refused before
pandas can rename them.

## Supported geographies and discovery

Only 2016 and 2021 GCP files are supported. Member years, source code headers,
cache identities, export filenames and manifest vintage follow the explicitly
selected year. Archives with mixed Census vintages or other profiles are refused;
no automatic historical joins or geography conversions are performed.

The examples below use 2021 identifiers except the labelled 2016 SSC row.

| Geography | Meaning | Example NSW source identifier |
| --- | --- | --- |
| POA | Postal Areas (postcode approximations) | POA2000 |
| SA1 | Statistical Areas Level 1 | 10201102801 |
| SA2 | Statistical Areas Level 2 | 101021007 |
| SA3 | Statistical Areas Level 3 | 10102 |
| SA4 | Statistical Areas Level 4 | 101 |
| LGA | Local Government Areas | LGA17200 |
| SED | State Electoral Divisions | SED10001 |
| CED | Commonwealth Electoral Divisions | CED101 |
| STE | States and Territories | 1 |
| SAL (2021) | Suburbs and Localities | SAL10001 |
| SSC (2016) | State Suburbs | SSC10001 |

The public `GEOGRAPHIES` mapping retains its 2021 meaning; API validation uses
the requested year. Codes are strings in returned data;
filters accept bare digits, the matching geography prefix, case variations and
surrounding whitespace. Integer filters work too; only POA accepts whole-number
floats and pads to four digits. Special numeric identifiers used for offshore,
migratory and no-usual-address areas are retained. Their meaning comes from ABS
geography metadata, not from guessing based on the number. No boundary allocation,
spatial joins or cross-vintage correspondence is performed.

The complete official 2021 DataPack contains
`Metadata/Metadata_2021_GCP_DataPack_R1_R2.xlsx`; 2016 uses
`Metadata/Metadata_2016_GCP_DataPack.xlsx`. Discovery reads the **Table Number,
Name, Population** and **Cell Descriptors Information** sheets, accepting their
observed capitalization differences. It needs no extra
Excel package and does not evaluate formulas. A missing, ambiguous or malformed
workbook raises an error; CSV-only extraction still works without the workbook.
The original short measure names remain unchanged in wide and long data.

```python
from abs_census_etl import list_census_tables, describe_census

tables = list_census_tables(geo="LGA", state="NSW", search="income")
variables = describe_census(
    geo="LGA", state="NSW", tables=["G02"], search="age",
)
print(tables)     # table, name, population, parts
print(variables)  # table, profile_table, datapack_table, variable, long_name, ...
```

Both functions accept `source_zip` and `cache_dir` just like extraction. Table
listing includes available logical tables, with split parts in `parts`. Variable
descriptions search all available tables by default; pass `tables` to narrow
them. They accept logical IDs such as G04 or an individual part such as G04A.
`table` is the logical ID, such as G09; `profile_table` preserves the workbook
value, such as G09a or G09b. Profile sections and data-file parts are different:
the real G09C data file contains variables from both G09a and G09b. The
`datapack_table` field identifies the CSV part. No lettered profile rows are
dropped when searching all tables. `long_name` is the official full descriptor,
while `description` is the profile's column-heading text (often just "Persons"). Search matches any returned field as
literal text, case-insensitively. An empty result is valid; missing table requests
still raise. These functions inspect metadata and member names, not measure rows.

CLI equivalents (source checkout, substitute the Windows environment interpreter):

```bash
.venv/bin/python abs_census_etl.py --geo LGA --state NSW --list-tables --search income
.venv/bin/python abs_census_etl.py --geo LGA --state NSW --describe --search median_age
```

Discovery emits tab-separated columns. `--list-tables`, `--describe` and
`--list-columns` are mutually exclusive. `--search` needs a metadata command;
metadata commands reject data-selection options `--codes`, `--keep` and `--long`.

## CLI

```bash
.venv/bin/abs-census-etl --geo POA --state NSW --tables G01 G02 --out abs_out
.venv/bin/abs-census-etl --geo SA1 --state NSW --tables G01 --format csv
.venv/bin/abs-census-etl --zip data.zip --geo POA --state NSW --tables G04 --long --format csv
.venv/bin/abs-census-etl --zip data.zip --geo POA --state NSW --tables G01 --list-columns
```

On Windows, use `.venv\Scripts\abs-census-etl.exe` instead. From another working
directory, use the absolute path to that environment's launcher. `python -m
abs_census_etl` also works with the environment's Python; direct `python
abs_census_etl.py` use from the checkout remains supported.

## Numeric and source integrity

All CSV fields are read as text before numeric conversion. After whitespace
trimming, only these case-insensitive tokens become missing measures: empty text,
`..`, `...`, `-`, `np`, `n.p.`, `na`, `n/a`, `null`, `none`. Unexpected tokens such
as `12a`, quoted `"1,234"`, `NaN` and infinity raise `ValueError` with the member,
column, geographic code and token. Finite decimal and scientific notation are
accepted. Measures are validated before filtering rows or selecting columns, so
bad values outside the requested area also fail. Blank or duplicate geography
codes remain errors. This is numeric parsing, not a semantic check that every
measure has the right unit, plausible magnitude or exact arbitrary precision.

Cached ZIPs and explicit `source_zip` / `--zip` archives receive the same quick
check of the central directory and each member's local header, with no payload
decompression. It refuses encrypted members and compression methods this Python
cannot read, such as Deflate64 from some re-zipping tools. As selected members
are read, Python's zipfile checks their CRCs. Corruption in an unread member may
remain undetected by a selective extraction; this is not a full archive audit.
New downloads get a full CRC scan once before entering the cache. This avoids
repeated scans of large national packs on every cache hit. Corrupt files are left
intact for inspection. Archive failures, including unreadable members, invalid
compressed streams, truncation and CRC errors, name the containing ZIP archive
and say to move it aside and download again, or supply a valid local archive.
The CLI prints a clean `ERROR:` rather than a decompression traceback. A
syntactically valid ZIP can still be the wrong pack; state/geography/member
checks then refuse it. A checksum is a content identifier, not independent proof
of ABS authenticity.

## Export formats and validation

CSV is the default for both the API and CLI. For Parquet, install the `parquet` extra
(`.[parquet]` in [Setup](../README.md#setup) for pip, or `--extra parquet` with uv in
[Platforms and development](development.md); managed installs repeat the README download
command with `--parquet` on Unix or `-Parquet` on Windows), then pass
`file_format="parquet"` in Python or `--format parquet` on the CLI.

**Default format change:** CSV replaces the earlier Parquet default. If you previously
exported Parquet, either keep passing `--format parquet` / `file_format="parquet"`, or
choose a fresh output directory for CSV. Export refuses to write when another format
exists for the same requested geography/state/table, in either wide or long form. It
checks all requested tables before accessing the source archive or downloading, and
leaves old files and the manifest intact on a conflict. No exports are automatically
deleted. The CLI stores downloaded ZIPs in `<out>/raw/`; a different output
folder has a separate cache. Reuse the first archive with
`--zip abs_out/raw/2021_GCP_POA_for_NSW_short-header.zip` for an NSW POA export
to a new `--out` folder, or use an absolute ZIP path from another directory.
The Python API uses `cache_dir` instead and can reuse a shared cache or an
explicit `source_zip`. See [CHANGELOG.md](../CHANGELOG.md).

Before source access, export validates code/column selections, optional Parquet engine
availability, output path types, format collisions and symlinked output files. With an
explicit ZIP, destination checks also precede opening it. A folder that has a lock file is
locked first and checked once. A folder without one is checked without writing, then again
under the new lock, so changes after the first check may be refused after ZIP directory/
identity validation. Payload reads and downloads always follow the locked checks.
Scalar/empty `keep` values are invalid; use `keep=None` for all measures. Unknown column
names and unmatched geographic codes require reading the data. Pyarrow is the
recommended and integration-tested Parquet engine. Pandas may use fastparquet when
installed; preflight admits that fallback, but fastparquet exports have not been
integration-tested here.

Permission preflight checks existing destination files and the nearest existing output
directory with `os.access`. It is best effort: on POSIX, effective permissions normally
apply, while root may bypass mode bits but still encounters read-only mounts or other OS
restrictions. On Windows, directory checks do not inspect ACLs and report folders as
writable; file checks detect read-only flags, not Excel locks. These checks cannot
guarantee early refusal for every write failure. A regular-file output path or ancestor
raises `NotADirectoryError`. If no existing ancestor can be found before a self-parent
root, export raises `FileNotFoundError` with drive/share guidance instead of repeatedly
walking an unavailable Windows drive or network share.

Symlinked manifest/data destination files are refused explicitly before source access.
Their referents are not modified. An output-directory link that resolves to a directory
remains usable. This prevents atomic replacement from silently turning a file symlink
into a regular file.

## Manifest paths

The geography/state manifest retains the **latest export entry for each table**,
including tables exported by earlier calls in a different format. Each entry has its
file name, source archive/URL, format, creation time, shape, rows and columns. The
`file` value is relative to the manifest's directory, so resolve it with
`manifest_path.parent / entry["file"]`, regardless of the working directory. Legacy
absolute/cwd-relative export paths are converted to basenames when the manifest is next
updated. The returned export paths are unchanged. New source-archive provenance is an
absolute resolved path. Each new table entry and the latest-call top level include
`source_sha256`, the SHA-256 of the ZIP bytes read. Hashing and extraction use the
same open file; another hash after reading detects an in-place source change
before publication. Retained entries keep their original hashes even when a
source path now holds different bytes. Entries from older manifests without a
hash receive JSON `null`; the current contents of an old path cannot prove its
historical identity. Retained legacy relative sources keep their original text and
are marked `source_path_kind="legacy_cwd_relative"`; their old working directory cannot
be reconstructed reliably. Absolute labels from earlier exports are preserved;
unlabelled paths are checked using both POSIX and Windows path syntax, independently of
the current operating system. Top-level source/format/time fields describe the most
recent call only; use the per-table entries for earlier exports. Re-exporting a table
replaces its entry. This is not an inventory of every file: older wide/long variants can
remain on disk without a separate entry, and missing or manually edited files are not
reconciled. Invalid or incompatible existing manifests raise before source access.
Errors explain how to recover: use a fresh output folder, or remove only the JSON
manifest and rerun. Removing it discards the inventory of earlier exports; it does not
delete CSV/Parquet data. The new manifest will list only tables exported after recovery.
Nothing is removed automatically.

## Concurrent exports

Cooperating exports acquire an exclusive operating-system lock on the output
folder's persistent `.abs-census-etl.lock` file. It covers the final recovery/
destination checks, the authoritative previous-manifest read, loading the source,
staging, publication and cleanup. When the folder already has its lock file, a busy
refusal precedes source access, including with `--zip` / `source_zip`, and a running
export's staging folders and recovery journal are only inspected under the lock. Only
a folder without a lock file gets unlocked destination checks and quick explicit-ZIP
directory/identity validation before the lock is created, so an invalid ZIP leaves no
new lock. If another export starts meanwhile, its busy message follows that ZIP access.
No ZIP member payload reads or downloads happen before acquiring the lock. Wait for
the other export to finish, then retry. The OS
releases the lock on exceptions or process termination. The empty lock file
stays in place deliberately; **do not delete it**, since replacing its inode can
allow two processes to lock different files. A missing/non-file explicit ZIP path
or invalid ZIP directory is rejected before creating a fresh output folder.
Existing-folder destination checks retain priority over opening an explicit source;
without a lock file they repeat under the new lock to catch subsequent changes.
Errors that require reading member payloads (damaged contents, wrong state or
unmatched codes) can leave
a new folder containing just this coordination file; no data was published.
Removing it on error would race other exporters, so it is not deleted.

This protects cooperating application writers, not arbitrary programs editing
files or readers requiring an atomic multi-file snapshot. Network filesystem
locking semantics depend on the OS and server; use a local output directory if
reliable advisory locks are unavailable. Only would-block errors report a busy
folder. Unsupported/unavailable locking reports the underlying OS error and the
local-folder remedy instead of telling you to retry forever. Existing lock files
are opened read-only so shared users do not need write permission on that file;
creating the first lock and writing exports still require the appropriate output
folder/file permissions. The lock is not a recovery journal and its presence
alone does not mean an export is running.

## Export recovery

All table files and the JSON manifest are written in a private staging directory under
the output folder before any published file is changed. Staged data and manifest
payloads use `.part` suffixes, so recursive `*.csv`/`*.parquet` searches do not find
unpublished data. Existing output files are backed up before publication. Data files are
replaced one at a time; the manifest is published last. Catchable write/publication
errors, including Ctrl-C, leave or restore the previous files and inventory; a failed
first export removes newly published files. This handles late lock and disk-full errors
that preflight cannot predict.

Data files and JSON are replaced by new filesystem objects, not overwritten in place.
Hard-linked copies and already-open handles can keep the old data; reopen the published
pathname for the new export. New ownership belongs to the exporting user, and previous
ownership, ACLs and extended attributes are not preserved. Only existing mode bits are
copied for data and JSON; a first manifest takes its mode from a new data file. No
process-wide umask change is made. The JSON write uses a temporary file, flush/fsync,
and atomic rename. Cleanup retries read-only temporary-file deletion when supported and
does not mask the original export error. Undeletable temporary files are ignored.

Recovery uses a manual journal at `.abs-export-*/recovery.json`. It records a per-file
pending name, available backup, and status. After rollback, `restored` means the
original was put back (or a newly created file removed), `rollback_failed` means manual
repair is needed, and `never_published` means that target was untouched. Consumed
backups are removed from the available map; backups for untouched files do not need
restoring.

Any retained journal blocks another export, including a malformed journal or one left
after completed publication/rollback cleanup. Existing journals are refused before
opening an explicit ZIP. In a folder without a lock file, a second check under the new
lock catches journals that appeared since that preflight, before any payload is read.
The check covers the
whole output folder, so one journal blocks exports for every geography and state written
there. The refusal names each journal's `state` (`unreadable` if it cannot be parsed).
This prevents a later export from making the saved backups stale. Verify the published
data and manifest, perform any required repairs, then remove or archive the whole
recovery directory outside the output folder. Do not delete the journal merely to bypass
the check. There is no automatic repair/replay.

A `committed` or `rolled_back` journal means no repair was needed, but cleanup could not
delete the journal, for example because another program held it open. Cleanup deletes
the journal before the other staging files, so an interrupted cleanup leaves at most a
folder without a journal. When cleanup leaves anything behind, a successful export emits
a `RuntimeWarning` and a failed export adds the same text as a note to its error; the
CLI prints these as `WARNING:` lines or after the `ERROR:` line. Orphan staging
folders also warn during destination preflight, including when a format conflict
then refuses the export. Repeated checks do not repeat the same orphan warning.

If journal updates fail, the error warns that the journal may be stale and keeps the
recovery directory. If SIGKILL or power loss interrupts publication, the last
`publishing` status may be uncertain: inspect pending files, backups, and published
targets before repairing. This is not an atomic snapshot for concurrent readers
or non-cooperating writers,
and no power-loss durability guarantee is made. A crash during staging before the
journal exists has not published anything. Export never opens or deletes files in
`.abs-export-*` folders without a journal, but it warns about them with
`RuntimeWarning`, listing each one: they can hold full-size `.part` payloads. Delete
them only when no export is running, since an export still in progress also has such a
folder. Extra staging/backup space is required.

### Regenerate instead of repairing manually

For regenerable extracts, the simplest recovery is to leave the affected folder
intact and export to a **new folder**, supplying the original archive with
`--zip old_out/raw/2021_GCP_POA_for_NSW_short-header.zip` (or `source_zip` in
Python) to avoid another download. Reuse the original selection and format.
Check the new results before pointing downstream jobs at them. Keep the old
folder and recovery backups until satisfied; do not mix old and new outputs or
copy an uncertain manifest over the new one. If retaining the old output folder,
follow the journal inspection/repair steps above, then archive its recovery
directory outside the output folder. There is no automatic replay or deletion
of recovery data.

## Geography/data caveats

* ABS POAs approximate Australia Post postcodes; they do not perfectly align.
* SA1 is smaller than POA, but most detailed 2021 Census demographic tables do
  **not** go down to Mesh Block level.
* Census 2016 uses ASGS Edition 2; Census 2021 uses ASGS Edition 3. Use an explicit geographic
  correspondence for other vintages or boundaries.
* Random ABS confidentiality adjustments can affect small-area counts. Do not
  sum rates, medians or percentages as if they were additive.

ABS DataPacks: https://www.abs.gov.au/census/find-census-data/datapacks


[asgs-2016]: https://www.abs.gov.au/ausstats/abs%40.nsf/mf/1270.0.55.001
