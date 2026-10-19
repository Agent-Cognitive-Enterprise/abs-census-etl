# ABS Census ETL, Python module and CLI

Reads **2021 Census General Community Profile (GCP)** CSVs from official ABS
DataPack ZIP archives at **POA** (ABS postal area) or **SA1** (small
statistical area) level. The module exposes reusable functions and retains a
CLI. It processes table parts such as `G04A` and `G04B` as one table, keyed on
geography, with checks to prevent silent mismatches. Geography codes remain
strings to retain leading zeros.

## Setup

The verified environment is **Python 3.14.7**, pandas 3.0.6, and requests
2.34.2 (with pyarrow 26.0.0 for Parquet). Other Python/dependency versions
have not yet been validated; a broader support matrix and lockfile are planned
in slice 2.

With Python 3.14 installed, open a terminal in the project directory and
create the project environment first.

Linux/macOS:

```bash
python3.14 -m venv .venv
.venv/bin/python -m pip install "pandas==3.0.6" "requests==2.34.2"
# Optional Parquet engine:
.venv/bin/python -m pip install "pyarrow==26.0.0"
```

Windows PowerShell:

```powershell
py -3.14 -m venv .venv
.venv\Scripts\python.exe -m pip install "pandas==3.0.6" "requests==2.34.2"
# Optional Parquet engine:
.venv\Scripts\python.exe -m pip install "pyarrow==26.0.0"
```

Save `abs_census_etl.py` in your project or add its directory to your Python
path. Importing the module does **not** download anything, create files, or
execute CLI code.

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
.venv/bin/python abs_census_etl.py --geo POA --state NSW --tables G01 G02 --out abs_out
.venv/bin/python abs_census_etl.py --geo SA1 --state NSW --tables G01 --format csv
.venv/bin/python abs_census_etl.py --zip data.zip --geo POA --tables G04 --long --format csv
.venv/bin/python abs_census_etl.py --zip data.zip --geo POA --tables G01 --list-columns
```

On Windows, replace `.venv/bin/python` in the CLI examples with
`.venv\Scripts\python.exe`. No environment activation is required.

CSV is the default for both the API and CLI. For Parquet, install `pyarrow`
and pass `file_format="parquet"` in Python or `--format parquet` on the CLI.

**Default format change (unreleased):** CSV replaces the earlier Parquet
default. No numbered release has been established yet; slice 2 will define
version metadata. If you previously exported Parquet, either keep passing
`--format parquet` / `file_format="parquet"`, or choose a fresh output
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

Both commands use the project environment. The dedicated POSIX data-mode
mutation is explicitly skipped on platforms without POSIX mode semantics;
skipped probes are not counted as caught. The mutation script reports missing
dependencies as a setup error before trying any mutations.

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

## Geography/data caveats

* ABS POAs approximate Australia Post postcodes; they do not perfectly align.
* SA1 is smaller than POA, but most detailed 2021 Census demographic tables do
  **not** go down to Mesh Block level.
* The 2021 Census uses ASGS Edition 3. Use an explicit geographic
  correspondence for other vintages or boundaries.
* Random ABS confidentiality adjustments can affect small-area counts. Do not
  sum rates, medians or percentages as if they were additive.

ABS DataPacks: https://www.abs.gov.au/census/find-census-data/datapacks
