#!/usr/bin/env python3
"""Read and transform ABS 2021 Census GCP DataPacks from Python or the CLI.

The General Community Profile (GCP) is available for Postal Areas (POA,
postcode approximations) and Statistical Areas Level 1 (SA1). Tables with
lettered parts, such as G04A/G04B, are automatically joined on the geographic
code. Geographic identifiers are kept as strings to preserve leading zeros.

Dependencies: see README.md for the tested Python environment
Optional Parquet exports: install the project's parquet extra; see README.md
(Install or update for managed installs, Setup for pip, Platforms and
development for uv).

Python API::

    from abs_census_etl import load_census, load_census_tables, export_census

    df = load_census(geo="POA", state="NSW", table="G01", codes=["2000"],
                     keep=["Tot_P_P"])
    tables = load_census_tables(geo="SA1", state="NSW", tables=["G01", "G04"])
    files = export_census(geo="POA", state="NSW", tables=["G01"],
                          out="abs_out", file_format="csv")

Use ``source_zip="/path/to/2021_GCP_...zip"`` in any of these functions to
work fully offline. Otherwise the DataPack is downloaded and cached in
``cache_dir`` (default: ``abs_cache``).

CLI examples::

    abs-census-etl --geo POA --state NSW --tables G01 G02 --out out
    abs-census-etl --geo SA1 --state NSW --tables G01 --out out
    abs-census-etl --zip local.zip --geo POA --tables G04 --long \
        --format csv --out out

Census 2021 uses ASGS Edition 3 boundaries. Do not treat ABS Postal Areas as
identical to Australia Post postcodes, or join incompatible vintages without a
correspondence. Census counts may include small confidentiality adjustments;
percentages, rates and medians should not simply be summed.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import stat
import sys
import tempfile
import warnings
from collections.abc import Iterable, Sequence
from datetime import datetime, timezone
from importlib.util import find_spec
from numbers import Integral, Real
from pathlib import Path, PurePosixPath, PureWindowsPath
from zipfile import BadZipFile, ZipFile

import pandas as pd
import requests

BASE_URL = "https://www.abs.gov.au/census/find-census-data/datapacks/download"
MISSING = {"", "..", "...", "-", "np", "n.p.", "na", "n/a", "null", "none"}
VALID_GEOS = {"POA", "SA1"}
VALID_STATES = {"AUS", "NSW", "VIC", "QLD", "SA", "WA", "TAS", "NT", "ACT", "OT"}
PARQUET_ENGINE_HELP = (
    "Parquet export needs pyarrow or fastparquet. Install the project's parquet "
    "extra as shown in README.md (Install or update for managed installs, "
    "Setup for pip, Platforms and development for uv). "
    "Or use --format csv (CLI) / file_format='csv' (Python)."
)


def args_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="abs-census-etl", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--geo", choices=sorted(VALID_GEOS), required=True)
    parser.add_argument("--state", default="NSW", choices=sorted(VALID_STATES))
    parser.add_argument("--tables", nargs="+", default=["G01"],
                        help="Table IDs, e.g. G01 G04 (G04 joins G04A and G04B)")
    parser.add_argument("--zip", dest="source_zip", type=Path,
                        help="Use a previously downloaded DataPack (no network)")
    parser.add_argument("--out", type=Path, default=Path("abs_out"))
    parser.add_argument("--format", choices=["parquet", "csv"], default="csv")
    parser.add_argument("--long", action="store_true",
                        help="Output [geography_code, variable, value] rather than wide columns")
    parser.add_argument("--keep", nargs="+", help="Only retain specified measure columns")
    parser.add_argument("--codes", nargs="+", help="Filter geographic codes (as strings)")
    parser.add_argument("--list-columns", action="store_true",
                        help="List columns instead of exporting tables")
    return parser


def _validate_geography(geo: str, state: str) -> tuple[str, str]:
    geo, state = geo.upper(), state.upper()
    if geo not in VALID_GEOS:
        raise ValueError(f"Unsupported geography {geo!r}. Choose from {sorted(VALID_GEOS)}")
    if state not in VALID_STATES:
        raise ValueError(f"Unsupported state {state!r}. Choose from {sorted(VALID_STATES)}")
    return geo, state


def _normalize_tables(tables: Iterable[str]) -> list[str]:
    if isinstance(tables, str):
        raise ValueError("tables must be a sequence, e.g. ['G01', 'G04'], not a string")
    normalized = list(dict.fromkeys(t.strip().upper() for t in tables))
    if not normalized:
        raise ValueError("At least one table ID is required")
    for table in normalized:
        if not re.fullmatch(r"G\d{2}[A-Z]?", table):
            raise ValueError(f"Invalid table ID {table!r}; expected e.g. 'G01' or 'G04A'")
    return normalized


def datapack_url(geo: str, state: str) -> str:
    geo, state = _validate_geography(geo, state)
    return f"{BASE_URL}/2021_GCP_{geo}_for_{state}_short-header.zip"


def download_if_needed(url: str, dest: Path) -> None:
    """Atomically cache a downloadable ZIP; no network call if already cached."""
    dest = Path(dest)
    if dest.is_file():
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=dest.parent, suffix=".part", delete=False) as f:
        tmp = Path(f.name)
    try:
        with requests.get(url, stream=True, timeout=(15, 120),
                          headers={"User-Agent": "abs-census-etl"}) as response:
            response.raise_for_status()
            with tmp.open("wb") as out:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        out.write(chunk)
        with ZipFile(tmp) as zipfile:
            damaged_member = zipfile.testzip()
            if damaged_member is not None:
                raise BadZipFile(f"Downloaded ZIP failed integrity check: {damaged_member}")
        tmp.replace(dest)
    finally:
        tmp.unlink(missing_ok=True)


def _resolve_source(*, geo: str, state: str,
                    source_zip: str | Path | None,
                    cache_dir: str | Path) -> Path:
    if source_zip is not None:
        source = Path(source_zip)
        if not source.is_file():
            raise FileNotFoundError(source)
        return source
    url = datapack_url(geo, state)
    cached = Path(cache_dir) / Path(url).name
    download_if_needed(url, cached)
    return cached


def find_members(z: ZipFile, table: str, geo: str,
                 state: str | None = None) -> list[str]:
    """Find a table within one state; infer state only when unambiguous."""
    table = table.upper()
    geo = geo.upper()
    if state is not None:
        geo, state = _validate_geography(geo, state)
    members: list[tuple[str, str, str]] = []
    for name in z.namelist():
        match = re.fullmatch(r"2021Census_(G\d{2}[A-Z]?)_([A-Z]+)_(POA|SA1)\.csv",
                             Path(name).name, re.I)
        if not match or match.group(3).upper() != geo:
            continue
        member_state = match.group(2).upper()
        # National download URLs use AUS; CSV filenames use AUST.
        if member_state == "AUST":
            member_state = "AUS"
        members.append((match.group(1).upper(), member_state, name))
    if state is None:
        candidate_states = {member_state for key, member_state, _ in members
                            if key == table or re.fullmatch(re.escape(table) + r"[A-Z]", key)}
        if len(candidate_states) > 1:
            raise ValueError(f"Ambiguous state for {table}: {sorted(candidate_states)}; "
                             "pass state explicitly")
        state = next(iter(candidate_states), None)
    by_id: dict[str, list[str]] = {}
    other_states: set[str] = set()
    other_ids: set[str] = set()
    for key, member_state, name in members:
        if state is not None and member_state != state:
            other_states.add(member_state)
            other_ids.add(key)
            continue
        by_id.setdefault(key, []).append(name)
    if state is not None and not by_id and other_states:
        raise ValueError(f"Archive has no {geo} tables for state {state}; "
                         f"available states: {sorted(other_states)}")
    if table in by_id:
        paths = by_id[table]
        if len(paths) != 1:
            raise ValueError(f"Ambiguous table {table}: {paths}")
        return paths
    parts = sorted((key, paths) for key, paths in by_id.items()
                   if re.fullmatch(re.escape(table) + r"[A-Z]", key))
    if parts:
        missing_parts = sorted(key for key in other_ids - by_id.keys()
                               if re.fullmatch(re.escape(table) + r"[A-Z]", key))
        if missing_parts:
            raise ValueError(f"Split parts of {table} missing for state {state}: "
                             f"{missing_parts}; found only for other states")
        if any(len(paths) != 1 for _, paths in parts):
            raise ValueError(f"Duplicate parts of {table}: {parts}")
        return [paths[0] for _, paths in parts]
    raise ValueError(f"Table {table} not found for {geo}. Available tables: "
                     + ", ".join(sorted(by_id)))


def load_csv(z: ZipFile, member: str, code: str) -> pd.DataFrame:
    with z.open(member) as source:
        raw_header = pd.read_csv(source, nrows=0).columns.tolist()
    header = [column.strip() for column in raw_header]
    if code not in header:
        raise ValueError(f"{member} has no {code}; header begins {header[:6]}")
    raw_code_column = raw_header[header.index(code)]
    with z.open(member) as source:
        df = pd.read_csv(source, dtype={raw_code_column: "string"}, low_memory=False)
    df.columns = df.columns.str.strip()
    df[code] = df[code].str.strip()
    if df[code].isna().any() or df[code].eq("").any() or df[code].duplicated().any():
        raise ValueError(f"Missing or repeated {code} in {member}")
    for col in df.columns:
        if col != code:
            raw = df[col].astype("string").str.strip()
            raw = raw.mask(raw.str.lower().isin(MISSING), pd.NA)
            df[col] = pd.to_numeric(raw, errors="coerce")
    return df


def combine_table(z: ZipFile, table: str, geo: str,
                  state: str | None = None) -> pd.DataFrame:
    """Join parts by code within one state; ambiguous states require an argument."""
    geo = geo.upper()
    code = f"{geo}_CODE_2021"
    merged: pd.DataFrame | None = None
    for member in find_members(z, table, geo, state):
        frame = load_csv(z, member, code)
        if merged is None:
            merged = frame
            continue
        # Split table parts should cover the same geography. Raise rather than
        # silently discarding measures from mismatched parts.
        if set(merged[code]) != set(frame[code]):
            raise ValueError(f"Split parts of {table} have different {code} values")
        shared = sorted((set(merged.columns) & set(frame.columns)) - {code})
        if shared:
            lhs = merged.set_index(code)[shared].sort_index()
            rhs = frame.set_index(code)[shared].sort_index()
            for column in shared:
                equal = (lhs[column].eq(rhs[column])
                         | (lhs[column].isna() & rhs[column].isna()))
                if not equal.fillna(False).all():
                    raise ValueError(f"Conflicting repeated column '{column}' in {table}")
            frame = frame.drop(columns=shared)
        merged = merged.merge(frame, on=code, how="inner", validate="one_to_one")
    assert merged is not None
    return merged


def _normalize_poa_code(value: str) -> str:
    value = value.strip().upper().removeprefix("POA")
    return value.zfill(4) if re.fullmatch(r"[0-9]{1,4}", value) else value


def _validate_codes(geo: str, codes: Sequence[str | int | float] | None) -> list[str] | None:
    """Validate/format inputs without opening an archive or downloading data."""
    if codes is None:
        return None
    if isinstance(codes, (str, bytes)):
        raise ValueError("codes must be a sequence, e.g. ['2000'], not a string")
    try:
        values = list(codes)
    except TypeError as exc:
        raise ValueError("codes must be a sequence of geographic codes") from exc
    if not values:
        raise ValueError("codes must contain at least one geographic code")
    requested = []
    for value in values:
        if isinstance(value, str):
            text = value
        elif isinstance(value, bool):
            raise ValueError(f"Invalid {geo} input: {value!r}; use a geographic code")
        elif geo == "POA" and isinstance(value, Real):
            if (not 0 <= value <= 9999 or not math.isfinite(value)
                    or value != int(value)):
                raise ValueError(f"Invalid POA input: {value!r}; use a finite whole postcode")
            text = str(int(value))
        elif geo == "SA1" and isinstance(value, Integral):
            text = str(value)
        else:
            raise ValueError(f"Invalid {geo} input: {value!r}; use a geographic code")
        normalized = _normalize_poa_code(text) if geo == "POA" else text.strip()
        pattern = r"[0-9]{4}" if geo == "POA" else r"[0-9]+"
        if not re.fullmatch(pattern, normalized):
            raise ValueError(f"Invalid {geo} input: {value!r}; use a geographic code")
        requested.append(text)
    return requested


def _validate_keep(keep: Sequence[str] | None) -> list[str] | None:
    """Check selection shape before source access; column existence needs data."""
    if keep is None:
        return None
    if isinstance(keep, (str, bytes)):
        raise ValueError("keep must be a sequence, e.g. ['Tot_P_P'], not a string")
    try:
        columns = list(keep)
    except TypeError as exc:
        raise ValueError("keep must be a sequence of column names") from exc
    if not columns or any(not isinstance(column, str) or not column.strip()
                          for column in columns):
        raise ValueError("keep must contain at least one nonblank column name")
    return list(dict.fromkeys(column.strip() for column in columns))


def _transform_table(df: pd.DataFrame, *, geo: str,
                     codes: Sequence[str] | None,
                     keep: Sequence[str] | None,
                     long: bool) -> pd.DataFrame:
    code = f"{geo}_CODE_2021"
    if codes is not None:
        requested = codes
        normalize = _normalize_poa_code if geo == "POA" else str.strip
        wanted = {normalize(value) for value in requested}
        available = df[code].map(normalize)
        available_codes = set(available)
        missing = sorted({value for value in requested if normalize(value) not in available_codes})
        if missing:
            raise ValueError(f"Unmatched {geo} code(s): {missing}; check codes and state")
        df = df.loc[available.isin(wanted)].copy()
    if keep is not None:
        unknown = sorted(set(keep) - set(df.columns))
        if unknown:
            raise ValueError(f"Unknown column(s): {unknown}; use --list-columns")
        df = df[[code, *dict.fromkeys(column for column in keep if column != code)]]
    if long:
        df = df.melt(id_vars=[code], var_name="variable", value_name="value")
    return df


def _read_tables_from_zip(*, source: Path, geo: str, state: str,
                          tables: Sequence[str],
                          codes: Sequence[str] | None,
                          keep: Sequence[str] | None,
                          long: bool) -> dict[str, pd.DataFrame]:
    result: dict[str, pd.DataFrame] = {}
    with ZipFile(source) as z:
        for table in tables:
            df = combine_table(z, table, geo, state)
            result[table] = _transform_table(df, geo=geo, codes=codes,
                                             keep=keep, long=long)
    return result


def load_census_tables(*, geo: str = "POA", state: str = "NSW",
                       tables: Sequence[str] = ("G01",),
                       source_zip: str | Path | None = None,
                       cache_dir: str | Path = "abs_cache",
                       codes: Sequence[str | int | float] | None = None,
                       keep: Sequence[str] | None = None,
                       long: bool = False) -> dict[str, pd.DataFrame]:
    """Return {table_id: DataFrame}, downloading/caching once if needed.

    ``geo`` is 'POA' or 'SA1'. Use string geographic codes to preserve leading
    zeros. POA filters accept both "2000" and "POA2000"; returned codes retain
    the source format. Integer and finite whole-number float POA inputs are
    padded to four digits. Any unmatched code raises ValueError, including
    partially matched requests. CSV members must match the requested state.
    Supply a predownloaded ``source_zip`` to ensure offline operation.
    Set ``long=True`` for geography_code/variable/value rows.
    """
    geo, state = _validate_geography(geo, state)
    table_ids = _normalize_tables(tables)
    codes = _validate_codes(geo, codes)
    keep = _validate_keep(keep)
    source = _resolve_source(geo=geo, state=state, source_zip=source_zip,
                             cache_dir=cache_dir)
    return _read_tables_from_zip(source=source, geo=geo, state=state, tables=table_ids,
                                 codes=codes, keep=keep, long=long)


def load_census(*, geo: str = "POA", state: str = "NSW",
                table: str = "G01", source_zip: str | Path | None = None,
                cache_dir: str | Path = "abs_cache",
                codes: Sequence[str | int | float] | None = None,
                keep: Sequence[str] | None = None,
                long: bool = False) -> pd.DataFrame:
    """Read one Census table directly into a pandas DataFrame.

    Example::

        df = load_census(geo='POA', state='NSW', table='G01',
                         codes=['2000'], keep=['Tot_P_P'])
    """
    table_id = _normalize_tables([table])[0]
    return load_census_tables(geo=geo, state=state, tables=[table_id],
                              source_zip=source_zip, cache_dir=cache_dir,
                              codes=codes, keep=keep, long=long)[table_id]


def _remove_temporary(path: Path) -> None:
    """Best-effort cleanup that never hides the error being propagated."""
    try:
        path.unlink(missing_ok=True)
    except PermissionError:
        # Windows refuses to delete read-only files; clear the flag and retry.
        try:
            path.chmod(stat.S_IWRITE | stat.S_IREAD)
            path.unlink(missing_ok=True)
        except OSError:
            pass
    except OSError:
        pass


def _write_manifest(path: Path, manifest: dict, *, permissions_from: Path) -> None:
    """Replace JSON atomically, preserving existing or data-file permission bits."""
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=f".{path.name}.", suffix=".tmp",
                                         delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(manifest, stream, indent=2)
            stream.flush()
            mode_source = path if path.exists() else permissions_from
            shutil.copymode(mode_source, temporary)
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            _remove_temporary(temporary)


class _ExportRecoveryError(OSError):
    """Publication failed and rollback needs manual recovery from backups."""


def _publish_staged(staged: dict[Path, Path], work: Path) -> None:
    """Publish manifest last and journal rollback results for manual recovery."""
    backups = {}
    # Complete backups before modifying any published path.
    for index, target in enumerate(staged):
        if target.exists():
            backup = work / f"backup-{index}"
            shutil.copy2(target, backup)
            backups[target] = backup
    recovery = {"state": "prepared", "files": {
        target.name: {"pending": pending.name, "status": "never_published",
                      "originally_existed": target in backups}
        for target, pending in staged.items()}, "backups": {}}
    journal = work / "recovery.json"
    mode_reference = next(iter(staged.values()))

    def save_recovery() -> None:
        recovery["backups"] = {target.name: backup.name
                               for target, backup in backups.items() if backup.exists()}
        for target in staged:
            recovery["files"][target.name]["backup"] = recovery["backups"].get(target.name)
        _write_manifest(journal, recovery, permissions_from=mode_reference)

    save_recovery()  # A journal exists before the first publication can happen.
    try:
        recovery["state"] = "publishing"
        for target, pending in staged.items():
            recovery["files"][target.name]["status"] = "publishing"
            save_recovery()
            pending.replace(target)
            recovery["files"][target.name]["status"] = "published"
            save_recovery()
        recovery["state"] = "committed"
        save_recovery()
    except BaseException as original:
        failures = []
        for target, pending in reversed(list(staged.items())):
            entry = recovery["files"][target.name]
            if pending.exists():
                entry["status"] = "never_published"
                continue
            # A completed rename consumes its pending file, even if a signal
            # arrives before the subsequent journal update.
            try:
                if target in backups:
                    backups[target].replace(target)
                else:
                    target.unlink(missing_ok=True)
                entry["status"] = "restored"
            except BaseException as error:
                entry["status"] = "rollback_failed"
                entry["error"] = str(error)
                failures.append(f"{target}: {error}")
        recovery["state"] = "rollback_incomplete" if failures else "rolled_back"
        journal_error = None
        try:
            save_recovery()
        except BaseException as error:
            journal_error = error
        if failures or journal_error is not None:
            problem = ("rollback was incomplete" if failures
                       else "rollback completed but its journal update failed")
            detail = (f" Journal may be stale: {journal_error}." if journal_error else "")
            raise _ExportRecoveryError(
                f"Export failed and {problem}. Recovery files: {work}. "
                "See recovery.json for per-file state and available backups. "
                "Do not rely on the manifest until recovery completes."
                + detail + " " + "; ".join(failures)) from original
        raise


def _recovery_journal(work: Path) -> Path | None:
    """Return a staging folder's journal; an uninspectable one counts as present."""
    journal = work / "recovery.json"
    try:
        journal.stat()
    except FileNotFoundError:
        return None
    except OSError:
        # An inaccessible recovery directory cannot be presumed safe.
        pass
    return journal


def _journal_state(journal: Path) -> str:
    try:
        state = json.loads(journal.read_text(encoding="utf-8")).get("state")
    except (OSError, ValueError, UnicodeError, AttributeError):
        return "unreadable"
    return state if isinstance(state, str) else "unreadable"


def _cleanup_export(work: Path) -> str | None:
    """Best effort only; describe leftovers instead of raising over an export error."""
    # Cleanup runs only when no recovery is needed. Removing the journal first
    # means an interrupted cleanup leaves at most a journal-less folder, which
    # later exports report, rather than a journal that refuses them.
    _remove_temporary(work / "recovery.json")
    try:
        for path in work.iterdir():
            _remove_temporary(path)
        work.rmdir()
    except OSError:
        pass
    if _recovery_journal(work) is not None:
        return (f"Could not remove recovery journal {work / 'recovery.json'}; later "
                f"exports to {work.parent} are refused until that folder is removed "
                "or archived outside the output folder.")
    if work.exists():
        return (f"Could not remove staging folder {work}; it has no recovery journal "
                "and can be deleted when no export is running.")
    return None


def export_census(*, geo: str = "POA", state: str = "NSW",
                  tables: Sequence[str] = ("G01",),
                  source_zip: str | Path | None = None,
                  cache_dir: str | Path = "abs_cache",
                  codes: Sequence[str | int | float] | None = None,
                  keep: Sequence[str] | None = None,
                  long: bool = False, out: str | Path = "abs_out",
                  file_format: str = "csv") -> dict[str, Path]:
    """Run extraction and write tables + a JSON manifest. Return output paths."""
    geo, state = _validate_geography(geo, state)
    table_ids = _normalize_tables(tables)
    codes = _validate_codes(geo, codes)
    keep = _validate_keep(keep)
    if file_format not in {"csv", "parquet"}:
        raise ValueError("file_format must be 'csv' or 'parquet'")
    out_dir = Path(out)
    existing_parent = out_dir
    while not existing_parent.exists():
        if existing_parent.is_symlink():
            raise NotADirectoryError(f"Output folder link is broken: {existing_parent}")
        parent = existing_parent.parent
        if parent == existing_parent:
            raise FileNotFoundError(f"No existing output ancestor: {out_dir}; "
                                    "check the drive or network share is available")
        existing_parent = parent
    if not existing_parent.is_dir():
        raise NotADirectoryError(f"Export output must be a directory: {existing_parent}")
    # Any retained journal is a conservative stop, even if malformed or its
    # last state looks complete: a human must verify and resolve it first.
    recovery_journals = []
    orphan_folders = []
    try:
        children = list(out_dir.iterdir())
    except FileNotFoundError:
        children = []
    for child in sorted(children):
        if not child.name.startswith(".abs-export-"):
            continue
        journal = _recovery_journal(child)
        if journal is None:
            orphan_folders.append(child)
        else:
            recovery_journals.append(journal)
    if recovery_journals:
        raise _ExportRecoveryError("Unresolved export recovery journal: "
                                   + ", ".join(f"{path} (state: {_journal_state(path)})"
                                               for path in recovery_journals)
                                   + ". Verify recovery, then remove or archive its "
                                   "directory outside the output folder before exporting.")
    if orphan_folders:
        # Never deleted here: another export may still be staging into one.
        warnings.warn("Staging folders without a recovery journal: "
                      + ", ".join(str(path) for path in orphan_folders)
                      + ". They hold unpublished files from an interrupted export or "
                      "one still running; delete them when no export is running.",
                      RuntimeWarning, stacklevel=2)
    # Preflight every table before writing: old other-format exports must not
    # remain silently in use by downstream consumers during format migration.
    other_format = "parquet" if file_format == "csv" else "csv"
    conflicts = [out_dir / f"2021_GCP_{geo}_{state}_{table}_{shape}.{other_format}"
                 for table in table_ids for shape in ("wide", "long")
                 if (out_dir / f"2021_GCP_{geo}_{state}_{table}_{shape}.{other_format}").exists()]
    if conflicts:
        raise FileExistsError("Other-format exports already exist: "
                              + ", ".join(str(path) for path in conflicts) + ". "
                              "Use a fresh output directory or keep the existing format.")
    manifest_path = out_dir / f"2021_GCP_{geo}_{state}_manifest.json"
    targets = {table: out_dir / (f"2021_GCP_{geo}_{state}_{table}_"
                                 f"{'long' if long else 'wide'}.{file_format}")
               for table in table_ids}
    destinations = [manifest_path, *targets.values()]
    links = [path for path in destinations if path.is_symlink()]
    if links:
        raise ValueError("Symlinked export files are not supported: "
                         + ", ".join(str(path) for path in links))
    for path in destinations:
        if path.exists() and not path.is_file():
            raise IsADirectoryError(f"Export file target is not a regular file: {path}")
    # Best effort: Windows directory ACLs and file locks are not exposed by
    # os.access. Actual staging/publication errors still need recovery.
    read_only = [path for path in destinations
                 if path.exists() and not os.access(path, os.W_OK)]
    if not os.access(existing_parent, os.W_OK | os.X_OK):
        read_only.insert(0, existing_parent)
    if read_only:
        raise PermissionError("Read-only export output: "
                              + ", ".join(str(path) for path in read_only) + ". "
                              "Make it writable or use a fresh output folder.")
    if (file_format == "parquet" and find_spec("pyarrow") is None
            and find_spec("fastparquet") is None):
        raise ValueError(PARQUET_ENGINE_HELP)
    retained_tables = {}
    if manifest_path.exists():
        try:
            previous = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (ValueError, UnicodeError) as exc:
            raise ValueError(f"Invalid existing manifest: {manifest_path}. "
                             "Remove only this manifest or use a fresh output folder; "
                             "removing it discards prior inventory, not exported data.") from exc
        if (not isinstance(previous, dict)
                or previous.get("geography") != geo or previous.get("state") != state
                or previous.get("census_year") != 2021 or previous.get("asgs_edition") != 3
                or not isinstance(previous.get("tables"), dict)
                or any(not isinstance(entry, dict) or not isinstance(entry.get("file"), str)
                       for entry in previous["tables"].values())):
            raise ValueError(f"Incompatible existing manifest: {manifest_path}. "
                             "Remove only this manifest or use a fresh output folder; "
                             "removing it discards prior inventory, not exported data.")
        for table, entry in previous["tables"].items():
            # Older manifests kept provenance only at the top level. Backfill
            # those fields before combining entries from separate export calls.
            retained = dict(entry)
            # Handle legacy absolute paths from either operating-system family.
            retained["file"] = PureWindowsPath(retained["file"]).name
            for field in ("source", "source_url", "created_utc", "file_format"):
                retained.setdefault(field, previous.get(field))
            if retained.get("file_format") is None:
                retained["file_format"] = Path(retained["file"]).suffix.lstrip(".")
            if "shape" not in retained:
                stem = Path(retained["file"]).stem
                retained["shape"] = "long" if stem.endswith("_long") else "wide"
            provenance = retained.get("source")
            if isinstance(provenance, str):
                absolute = (retained.get("source_path_kind") == "absolute"
                            or PurePosixPath(provenance).is_absolute()
                            or PureWindowsPath(provenance).is_absolute())
                retained["source_path_kind"] = "absolute" if absolute else "legacy_cwd_relative"
            retained_tables[table] = retained
    source = _resolve_source(geo=geo, state=state, source_zip=source_zip,
                             cache_dir=cache_dir).resolve()
    data = _read_tables_from_zip(source=source, geo=geo, state=state, tables=table_ids,
                                 codes=codes, keep=keep, long=long)
    out_dir.mkdir(parents=True, exist_ok=True)
    url = datapack_url(geo, state)
    manifest = {"source": str(source), "source_url": url if source_zip is None else None,
                "source_path_kind": "absolute", "file_format": file_format,
                "manifest_scope": "latest_export_per_table",
                "census_year": 2021, "asgs_edition": 3, "geography": geo,
                "state": state, "created_utc": datetime.now(timezone.utc).isoformat(),
                "tables": retained_tables}
    work = Path(tempfile.mkdtemp(dir=out_dir, prefix=".abs-export-"))
    staged = {}
    try:
        code = f"{geo}_CODE_2021"
        for table_id, df in data.items():
            target = targets[table_id]
            pending = work / f"{target.name}.part"
            if file_format == "parquet":
                try:
                    df.to_parquet(pending, index=False)
                except ImportError as exc:
                    raise ValueError(PARQUET_ENGINE_HELP) from exc
            else:
                df.to_csv(pending, index=False)
            if target.exists():
                shutil.copymode(target, pending)
            staged[target] = pending
            n_areas = df[code].nunique() if long else len(df)
            manifest["tables"][table_id] = {
                "file": target.name, "areas": n_areas,
                "rows": len(df), "columns": df.columns.tolist(),
                "source": str(source), "source_url": manifest["source_url"],
                "source_path_kind": "absolute",
                "created_utc": manifest["created_utc"], "file_format": file_format,
                "shape": "long" if long else "wide",
            }
        pending_manifest = work / f"{manifest_path.name}.part"
        mode_source = manifest_path if manifest_path.exists() else next(iter(staged.values()))
        _write_manifest(pending_manifest, manifest, permissions_from=mode_source)
        staged[manifest_path] = pending_manifest
        _publish_staged(staged, work)
    except _ExportRecoveryError:
        raise  # Keep the recovery directory for manual repair.
    except BaseException as error:
        leftover = _cleanup_export(work)
        if leftover:
            # A note, not a new exception, so the export error stays visible.
            error.add_note(leftover)
        raise
    leftover = _cleanup_export(work)
    if leftover:
        warnings.warn(leftover, RuntimeWarning, stacklevel=2)
    return targets


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point; accepts an argument list for testing/embedding."""
    args = args_parser().parse_args(argv)
    if args.list_columns:
        data = load_census_tables(geo=args.geo, state=args.state,
                                  tables=args.tables, source_zip=args.source_zip,
                                  cache_dir=args.out / "raw")
        code = f"{args.geo}_CODE_2021"
        for table, df in data.items():
            print(f"\n{table} [{args.geo}]: {len(df):,} areas, "
                  f"{len(df.columns) - 1} measures")
            print("\n".join(c for c in df.columns if c != code))
        return 0
    with warnings.catch_warnings(record=True) as caught:
        try:
            outputs = export_census(geo=args.geo, state=args.state, tables=args.tables,
                                    source_zip=args.source_zip,
                                    cache_dir=args.out / "raw", codes=args.codes,
                                    keep=args.keep, long=args.long, out=args.out,
                                    file_format=args.format)
        finally:
            for warning in caught:
                print(f"WARNING: {warning.message}", file=sys.stderr)
    for table, path in outputs.items():
        print(f"{table}: {path}")
    print(f"Manifest: {args.out / f'2021_GCP_{args.geo}_{args.state}_manifest.json'}")
    return 0


def cli(argv: Sequence[str] | None = None) -> int:
    """Run the command with the same diagnostics for every launch method."""
    try:
        return main(argv)
    except (OSError, ValueError, requests.RequestException, BadZipFile) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        for note in getattr(exc, "__notes__", ()):
            print(note, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(cli())
