#!/usr/bin/env python3
"""Read and transform ABS 2016/2021 Census GCP DataPacks from Python or the CLI.

Default year is 2021. Explicit year=2016 reads the original 2016 GCP geography,
including its seven-digit SA1 identifiers. Both years support loading, metadata
discovery and CSV/Parquet exports. SSC is 2016-only; SAL is 2021-only.
Supported 2021 General Community Profile (GCP) geographies are POA, SA1–SA4,
LGA, SED, CED, STE and SAL. Postal Areas approximate postcodes. Tables with
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

Census 2016 uses ASGS Edition 2; Census 2021 uses ASGS Edition 3. Do not treat ABS Postal Areas as
identical to Australia Post postcodes, or join incompatible vintages without a
correspondence. Census counts may include small confidentiality adjustments;
percentages, rates and medians should not simply be summed.
"""

from __future__ import annotations

import argparse
import errno
import hashlib
import io
import json
import math
import os
import posixpath
import re
import shutil
import stat
import sys
import tempfile
import tomllib
import warnings
import zlib
from collections.abc import Callable, Iterable, Mapping, Sequence
from contextlib import contextmanager
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, distribution
from importlib.util import find_spec
from numbers import Integral, Real
from pathlib import Path, PurePosixPath, PureWindowsPath
from types import MappingProxyType
from typing import BinaryIO
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile

import pandas as pd
import requests

BASE_URL = "https://www.abs.gov.au/census/find-census-data/datapacks/download"
MISSING = {"", "..", "...", "-", "np", "n.p.", "na", "n/a", "null", "none"}
GEOGRAPHIES = {
    "POA": "Postal Areas", "SA1": "Statistical Area Level 1",
    "SA2": "Statistical Area Level 2", "SA3": "Statistical Area Level 3",
    "SA4": "Statistical Area Level 4", "LGA": "Local Government Areas",
    "SED": "State Electoral Divisions", "CED": "Commonwealth Electoral Divisions",
    "STE": "States and Territories", "SAL": "Suburbs and Localities",
}
VALID_GEOS = frozenset(GEOGRAPHIES)
MEMBER_RE = re.compile(r"2021Census_(G\d{2}[A-Z]?)_([A-Z]+)_([A-Z0-9]+)\.csv", re.I)
VALID_STATES = {"AUS", "NSW", "VIC", "QLD", "SA", "WA", "TAS", "NT", "ACT", "OT"}

# The public GEOGRAPHIES/VALID_GEOS/MEMBER_RE above retain their 2021 meanings.
_VINTAGES = MappingProxyType({
    2021: MappingProxyType({"profile": "GCP", "asgs_edition": 3,
        "table_count": 62, "variable_count": 16984, "columns": MappingProxyType(
        {geo: f"{geo}_CODE_2021" for geo in GEOGRAPHIES}),
        "national_tokens": frozenset({"AUS", "AUST"}), "member_re": MEMBER_RE}),
    2016: MappingProxyType({"profile": "GCP", "asgs_edition": 2,
        "table_count": 59, "variable_count": 15535, "columns": MappingProxyType(
        {"POA": "POA_CODE_2016", "SA1": "SA1_7DIGITCODE_2016",
         "SA2": "SA2_MAINCODE_2016", "SA3": "SA3_CODE_2016", "SA4": "SA4_CODE_2016",
         "LGA": "LGA_CODE_2016", "SED": "SED_CODE_2016", "CED": "CED_CODE_2016",
         "SSC": "SSC_CODE_2016", "STE": "STE_CODE_2016"}), "national_tokens": frozenset({"AUS"}),
        "member_re": re.compile(
            r"2016Census_(G\d{2}[A-Z]?)_([A-Z]+)_([A-Z0-9]+)\.csv", re.I)}),
})
_ARCHIVE_MEMBER_RE = re.compile(
    r"(\d{4})Census_([A-Z]\d{2}[A-Z]?)_([A-Z]+)_([A-Z0-9]+)\.csv", re.I)
_METADATA_ID_RE = re.compile(
    r"Metadata_(\d{4})_([A-Z0-9]+)_DataPack(?:_[A-Za-z0-9]+)*\.xlsx", re.I)


def _vintage(year: int) -> Mapping:
    if not isinstance(year, Integral) or year not in _VINTAGES:
        raise ValueError(f"Unsupported Census year {year!r}; use integer 2016 or 2021")
    return _VINTAGES[year]


def _code_column(geo: str, year: int) -> str:
    return _vintage(year)["columns"][geo]


def _member_state(token: str, year: int) -> str:
    if token in {"AUS", "AUST"}:
        if token not in _vintage(year)["national_tokens"]:
            raise ValueError(f"Invalid national member token {token!r} for {year}; "
                             f"expected {sorted(_vintage(year)['national_tokens'])}")
        return "AUS"
    return token


def _check_archive(archive: ZipFile, year: int, *, geo: str | None = None,
                   suggest_year: bool = True) -> None:
    """Check source identity from names only, before reading any table payload."""
    requested = _vintage(year)["profile"]
    years, profiles = set(), set()
    members_found = False
    families = {"G": "GCP", "T": "TSP", "I": "IP", "W": "WPP", "P": "PEP"}
    for name in archive.namelist():
        member = _ARCHIVE_MEMBER_RE.fullmatch(Path(name).name)
        metadata = _METADATA_ID_RE.fullmatch(Path(name).name)
        if member:
            members_found = True
            years.add(int(member[1]))
            family = member[2][0].upper()
            profiles.add(families.get(family, f"unidentified table family {family}"))
        if metadata:
            years.add(int(metadata[1]))
            profiles.add(metadata[2].upper())
    if not members_found:
        raise ValueError(f"Unsupported/unidentified DataPack: requested {year} {requested}; "
                         f"found years {sorted(years)}, profiles {sorted(profiles)} and no "
                         "recognized Census table members. Supply a supported GCP archive.")
    if len(years) > 1:
        raise ValueError(f"Mixed Census years in archive: {sorted(years)}; requested {year} "
                         f"{requested}. Use a single-year GCP archive.")
    if years != {year}:
        found = next(iter(years))
        hint = f"Supply a {year} {requested} pack"
        if found in _VINTAGES and profiles == {requested}:
            if geo is not None and geo not in _vintage(found)["columns"]:
                hint += (f" for {geo}; {found} supports only "
                         f"{sorted(_vintage(found)['columns'])} in this version")
            elif suggest_year:
                hint = f"Select --year {found} (Python: year={found}) for that pack"
        raise ValueError(f"Wrong Census year: requested {year} {requested}; found {found} "
                         f"profiles {sorted(profiles)}. {hint}.")
    if profiles != {requested}:
        raise ValueError(f"Wrong DataPack profile: requested {year} {requested}; found "
                         f"{year} profiles {sorted(profiles)}. Supply a {year} GCP pack; "
                         "other profiles are not supported.")


def _validate_source_identity(source: Path, year: int, *, geo: str | None = None,
                              suggest_year: bool = True) -> None:
    with _zip_read_errors(source), ZipFile(source) as archive:
        try:
            _check_archive(archive, year, geo=geo, suggest_year=suggest_year)
        except ValueError as error:
            raise ValueError(f"{source}: {error}") from error

PARQUET_ENGINE_HELP = (
    "Parquet export needs pyarrow or fastparquet. Install the project's parquet "
    "extra as shown in README.md (Install or update for managed installs, "
    "Setup for pip, Platforms and development for uv). "
    "Or use --format csv (CLI) / file_format='csv' (Python)."
)


def args_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="abs-census-etl", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--geo", choices=sorted(set().union(
        *(v["columns"] for v in _VINTAGES.values()))), required=True)
    parser.add_argument("--year", type=int, choices=sorted(_VINTAGES), default=2021,
                        help="Census GCP year (default 2021); geography choices depend on year")
    parser.add_argument("--state", default="NSW", choices=sorted(VALID_STATES))
    parser.add_argument("--tables", nargs="+",
                        help="Table IDs, e.g. G01 G04; defaults to G01, or all for --describe")
    parser.add_argument("--zip", dest="source_zip", type=Path,
                        help="Use a previously downloaded DataPack (no network)")
    parser.add_argument("--out", type=Path, default=Path("abs_out"))
    parser.add_argument("--format", choices=["parquet", "csv"], default="csv")
    parser.add_argument("--long", action="store_true",
                        help="Output [geography_code, variable, value] rather than wide columns")
    parser.add_argument("--keep", nargs="+", help="Only retain specified measure columns")
    parser.add_argument("--codes", nargs="+", help="Filter geographic codes (as strings)")
    discovery = parser.add_mutually_exclusive_group()
    discovery.add_argument("--list-columns", action="store_true",
                        help="List columns instead of exporting tables")
    discovery.add_argument("--list-tables", action="store_true",
                           help="List available tables, names and populations")
    discovery.add_argument("--describe", action="store_true",
                           help="Describe variables in --tables using ABS metadata")
    parser.add_argument("--search", help="Case-insensitive literal search with --describe/--list-tables")
    return parser


def _validate_geography(geo: str, state: str, *, year: int = 2021) -> tuple[str, str]:
    vintage = _vintage(year)
    geo, state = geo.strip().upper(), state.strip().upper()
    if geo not in vintage["columns"]:
        raise ValueError(f"Unsupported geography {geo!r} for {year} GCP. "
                         f"Choose from {sorted(vintage['columns'])}")
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


def datapack_url(geo: str, state: str, *, year: int = 2021) -> str:
    geo, state = _validate_geography(geo, state, year=year)
    return f"{BASE_URL}/{year}_GCP_{geo}_for_{state}_short-header.zip"


def download_if_needed(url: str, dest: Path, *,
                       validate: Callable[[Path], None] | None = None) -> None:
    """Check cached ZIP directories; fully validate new downloads before caching."""
    dest = Path(dest)
    if dest.is_file():
        _validate_zip(dest)
        if validate is not None:
            validate(dest)
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
        try:
            _validate_zip(tmp, full=True)
        except BadZipFile as error:
            # The temporary file is removed below, so name the cache entry instead.
            raise BadZipFile(f"Downloaded DataPack rejected for {dest}: invalid ZIP "
                             f"({error.__cause__}). No cache entry was published. Retry the "
                             f"download; if it repeats, report this error and the source "
                             f"URL: {url}") from error
        if validate is not None:
            validate(tmp)
        tmp.replace(dest)
    finally:
        tmp.unlink(missing_ok=True)


def _zip_error(source: Path | BinaryIO | ZipFile, error: Exception) -> BadZipFile:
    if isinstance(source, ZipFile):
        name = source.filename
    elif isinstance(source, (str, os.PathLike)):
        name = source
    else:
        name = getattr(source, "name", None)
    return BadZipFile(f"Invalid ZIP {name or '<in-memory archive>'}: {error}. "
                      "Move it aside and download again, or supply a valid --zip / source_zip.")


@contextmanager
def _zip_read_errors(source: Path | BinaryIO | ZipFile):
    """Translate archive corruption only; retain numeric/schema diagnostics."""
    try:
        yield
    except (BadZipFile, zlib.error, EOFError) as exc:
        raise _zip_error(source, exc) from exc


def _validate_zip(path: Path, *, full: bool = False) -> None:
    try:
        with ZipFile(path) as archive:
            # Opening reads only local headers, without decompressing payloads, but
            # rejects encrypted members and compression methods this Python lacks.
            for info in archive.infolist():
                with archive.open(info):
                    pass
            if full:
                damaged = archive.testzip()
                if damaged:
                    raise BadZipFile(f"CRC failure in {damaged}")
    except (BadZipFile, EOFError, zlib.error, RuntimeError, NotImplementedError) as exc:
        raise _zip_error(path, exc) from exc


def _resolve_source(*, geo: str, state: str,
                    source_zip: str | Path | None,
                    cache_dir: str | Path, year: int = 2021) -> Path:
    if source_zip is not None:
        source = Path(source_zip)
        if not source.is_file():
            raise FileNotFoundError(source)
        _validate_zip(source)
        _validate_source_identity(source, year, geo=geo)
        return source
    url = datapack_url(geo, state, year=year)
    cached = Path(cache_dir) / Path(url).name

    def validate_cached(path: Path) -> None:
        with _zip_read_errors(path), ZipFile(path) as archive:
            try:
                _check_archive(archive, year, geo=geo, suggest_year=False)
            except ValueError as error:
                detail = str(error).rstrip(". ")
                if path == cached:
                    raise ValueError(f"Invalid cached DataPack {cached}: {detail}. "
                                     "Move it aside and retry to download the requested pack.") from error
                raise ValueError(f"Downloaded DataPack rejected for {cached}: {detail}. "
                                 "No cache entry was published. Retry the download; if it "
                                 f"repeats, report this error and the source URL: {url}") from error

    download_if_needed(url, cached, validate=validate_cached)
    return cached


def find_members(z: ZipFile, table: str, geo: str,
                 state: str | None = None, *, year: int = 2021) -> list[str]:
    """Find a table within one state; infer state only when unambiguous."""
    geo, _ = _validate_geography(geo, state or "NSW", year=year)
    if state is not None:
        _, state = _validate_geography(geo, state, year=year)
    _check_archive(z, year, geo=geo)
    return _find_members_validated(z, table, geo, state, year=year)


def _find_members_validated(z: ZipFile, table: str, geo: str,
                            state: str | None, *, year: int) -> list[str]:
    """Select parts after the caller validates this open archive's identity."""
    pattern = _vintage(year)["member_re"]
    table = table.upper()
    members: list[tuple[str, str, str]] = []
    for name in z.namelist():
        match = pattern.fullmatch(Path(name).name)
        if not match or match.group(3).upper() != geo:
            continue
        member_state = match.group(2).upper()
        member_state = _member_state(member_state, year)
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
    if len(set(header)) != len(header):
        raise ValueError(f"Duplicate column names after trimming in {member}")
    with z.open(member) as source:
        df = pd.read_csv(source, dtype="string", keep_default_na=False,
                         na_filter=False, low_memory=False)
    df.columns = df.columns.str.strip()
    df[code] = df[code].str.strip()
    if df[code].isna().any() or df[code].eq("").any() or df[code].duplicated().any():
        raise ValueError(f"Missing or repeated {code} in {member}")
    for col in df.columns:
        if col != code:
            raw = df[col].astype("string").str.strip()
            raw = raw.mask(raw.str.lower().isin(MISSING), pd.NA)
            numeric = pd.to_numeric(raw, errors="coerce")
            invalid = raw.notna() & (numeric.isna() | numeric.isin([math.inf, -math.inf]))
            if invalid.any():
                index = invalid[invalid].index[0]
                raise ValueError(f"Invalid numeric value in {member}, column {col!r}, "
                                 f"{code}={df.at[index, code]!r}: {raw.at[index]!r}")
            df[col] = numeric
    return df


def combine_table(z: ZipFile, table: str, geo: str,
                  state: str | None = None, *, year: int = 2021) -> pd.DataFrame:
    """Join parts by code within one state; ambiguous states require an argument."""
    geo, _ = _validate_geography(geo, state or "NSW", year=year)
    if state is not None:
        _, state = _validate_geography(geo, state, year=year)
    _check_archive(z, year, geo=geo)
    return _combine_validated_table(z, table, geo, state, year=year)


def _combine_validated_table(z: ZipFile, table: str, geo: str,
                             state: str | None, *, year: int) -> pd.DataFrame:
    code = _code_column(geo, year)
    merged: pd.DataFrame | None = None
    for member in _find_members_validated(z, table, geo, state, year=year):
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


def _normalize_code(geo: str, value: str) -> str:
    if geo == "POA":
        return _normalize_poa_code(value)
    return value.strip().upper().removeprefix(geo)


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
        elif geo != "POA" and isinstance(value, Integral):
            text = str(value)
        else:
            raise ValueError(f"Invalid {geo} input: {value!r}; use a geographic code")
        normalized = _normalize_code(geo, text)
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
                     long: bool, year: int = 2021) -> pd.DataFrame:
    code = _code_column(geo, year)
    if codes is not None:
        requested = codes
        normalize = lambda value: _normalize_code(geo, value)
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


def _read_tables_from_zip(*, source: Path | BinaryIO, geo: str, state: str,
                          tables: Sequence[str],
                          codes: Sequence[str] | None,
                          keep: Sequence[str] | None,
                          long: bool, year: int = 2021) -> dict[str, pd.DataFrame]:
    result: dict[str, pd.DataFrame] = {}
    with _zip_read_errors(source), ZipFile(source) as z:
        _check_archive(z, year, geo=geo)
        for table in tables:
            df = _combine_validated_table(z, table, geo, state, year=year)
            result[table] = _transform_table(df, geo=geo, codes=codes,
                                             keep=keep, long=long, year=year)
    return result


def load_census_tables(*, geo: str = "POA", state: str = "NSW", year: int = 2021,
                       tables: Sequence[str] = ("G01",),
                       source_zip: str | Path | None = None,
                       cache_dir: str | Path = "abs_cache",
                       codes: Sequence[str | int | float] | None = None,
                       keep: Sequence[str] | None = None,
                       long: bool = False) -> dict[str, pd.DataFrame]:
    """Return {table_id: DataFrame}, downloading/caching once if needed.

    ``year`` is an integer, default 2021; supported GCP vintages are 2016 and 2021.
    ``geo`` is validated for the requested year. Use strings to preserve zeros.
    POA filters accept both "2000" and "POA2000"; returned codes retain
    the source format. Integer and finite whole-number float POA inputs are
    padded to four digits. Any unmatched code raises ValueError, including
    partially matched requests. CSV members must match the requested state.
    Supply a predownloaded ``source_zip`` to ensure offline operation.
    Set ``long=True`` for geography_code/variable/value rows.
    """
    geo, state = _validate_geography(geo, state, year=year)
    table_ids = _normalize_tables(tables)
    codes = _validate_codes(geo, codes)
    keep = _validate_keep(keep)
    source = _resolve_source(geo=geo, state=state, source_zip=source_zip,
                             cache_dir=cache_dir, year=year)
    return _read_tables_from_zip(source=source, geo=geo, state=state, tables=table_ids,
                                 codes=codes, keep=keep, long=long, year=year)


def load_census(*, geo: str = "POA", state: str = "NSW", year: int = 2021,
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
    return load_census_tables(geo=geo, state=state, year=year, tables=[table_id],
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


# Read only the two known ABS metadata sheets. Excel formulas are never evaluated.
_XLSX_NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def _xlsx_sheets(content: bytes) -> dict[str, list[list[str]]]:
    """Read text cells, shared/rich strings and sparse rows from an XLSX workbook."""
    try:
        with ZipFile(io.BytesIO(content)) as book:
            shared = []
            if "xl/sharedStrings.xml" in book.namelist():
                shared = ["".join(t.text or "" for t in item.findall(".//s:t", _XLSX_NS))
                          for item in ET.fromstring(book.read("xl/sharedStrings.xml"))]
            relations = {rel.get("Id"): rel for rel in ET.fromstring(
                book.read("xl/_rels/workbook.xml.rels"))}
            workbook = ET.fromstring(book.read("xl/workbook.xml"))
            result = {}
            wanted = {"Table Number, Name, Population", "Cell Descriptors Information"}
            for sheet in workbook.findall("s:sheets/s:sheet", _XLSX_NS):
                name = sheet.get("name", "")
                canonical = {item.casefold(): item for item in wanted}.get(name.casefold())
                if canonical is None:
                    continue
                name = canonical
                rel = relations[sheet.get(f"{{{_REL_NS}}}id")]
                target = rel.get("Target", "")
                path = posixpath.normpath(target.lstrip("/") if target.startswith("/")
                                          else posixpath.join("xl", target))
                if (rel.get("TargetMode") == "External" or not path.startswith("xl/")
                        or not rel.get("Type", "").endswith("/worksheet")):
                    raise ValueError("Unsupported metadata worksheet relationship")
                rows = []
                for row in ET.fromstring(book.read(path)).findall(
                        "s:sheetData/s:row", _XLSX_NS):
                    cells = {}
                    for cell in row.findall("s:c", _XLSX_NS):
                        address = re.fullmatch(r"([A-Z]+)[0-9]+", cell.get("r", ""))
                        if address is None:
                            raise ValueError("Missing metadata cell address")
                        column = 0
                        for letter in address.group(1):
                            column = column * 26 + ord(letter) - ord("A") + 1
                        if column > 16384:
                            raise ValueError("Invalid metadata column address")
                        if cell.find("s:f", _XLSX_NS) is not None:
                            raise ValueError("Formula cells are unsupported in ABS metadata")
                        value = cell.find("s:v", _XLSX_NS)
                        text = value.text or "" if value is not None else ""
                        if cell.get("t") == "s":
                            index = int(text)
                            if not 0 <= index < len(shared):
                                raise ValueError("Invalid metadata shared-string index")
                            text = shared[index]
                        elif cell.get("t") == "inlineStr":
                            text = "".join(t.text or "" for t in cell.findall(".//s:t", _XLSX_NS))
                        cells[column - 1] = text.strip()
                    rows.append([cells.get(i, "") for i in range(max(cells, default=-1) + 1)])
                if name in result:
                    raise ValueError(f"Duplicate metadata sheet: {name}")
                result[name] = rows
            if set(result) != wanted:
                raise ValueError("Missing required ABS metadata sheets")
            return result
    except (KeyError, IndexError, TypeError, ValueError, ET.ParseError, BadZipFile) as exc:
        raise ValueError(f"Invalid ABS metadata workbook: {exc}") from exc


def _metadata_records(rows: list[list[str]], headers: Sequence[str]) -> list[dict[str, str]]:
    normalize = lambda text: re.sub(r"\s+", "", text).casefold()
    required = [normalize(h) for h in headers]
    for index, row in enumerate(rows):
        names = [normalize(c) for c in row]
        if all(h in names for h in required):
            positions = [names.index(h) for h in required]
            return [{h: values[p] if p < len(values) else ""
                     for h, p in zip(headers, positions)} for values in rows[index + 1:]
                    if any(values)]
    raise ValueError(f"Unrecognized ABS metadata headers: expected {list(headers)}")


def _read_metadata(archive: ZipFile, *, year: int = 2021) -> tuple[pd.DataFrame, pd.DataFrame]:
    members = [n for n in archive.namelist() if re.fullmatch(
        rf"Metadata_{year}_GCP_DataPack(?:_[A-Za-z0-9]+)*\.xlsx", Path(n).name, re.I)]
    if len(members) != 1:
        raise ValueError(f"Expected exactly one Metadata_{year}_GCP_DataPack workbook; "
                         f"found {len(members)}. Supply the complete official DataPack.")
    with _zip_read_errors(archive):
        sheets = _xlsx_sheets(archive.read(members[0]))
    tables = _metadata_records(sheets["Table Number, Name, Population"],
                               ["Table Number", "Table Name", "Table population"])
    variables = _metadata_records(sheets["Cell Descriptors Information"],
        ["Sequential", "Short", "Long", "DataPack file", "Profile table",
         "Column heading description in profile"])
    table_rows = [{"table": r["Table Number"], "name": r["Table Name"],
                   "population": r["Table population"]} for r in tables
                  if re.fullmatch(r"G\d{2}", r["Table Number"])]
    variable_rows = [{"table": r["Profile table"][:3].upper(),
                      "profile_table": r["Profile table"],
                      "datapack_table": r["DataPack file"].upper(),
                      "variable": r["Short"], "long_name": r["Long"],
                      "description": r["Column heading description in profile"],
                      "sequential": r["Sequential"]} for r in variables]
    t = pd.DataFrame(table_rows, columns=["table", "name", "population"])
    v = pd.DataFrame(variable_rows, columns=["table", "profile_table", "datapack_table", "variable",
                                            "long_name", "description", "sequential"])
    if (t.empty or v.empty or t["table"].duplicated().any()
            or v.duplicated(["datapack_table", "variable"]).any()
            or not set(v["table"]) <= set(t["table"])
            or not v["profile_table"].str.fullmatch(r"G\d{2}[A-Za-z]?", case=False).all()
            or not v["datapack_table"].str.fullmatch(r"G\d{2}[A-Z]?").all()
            or not v["datapack_table"].str[:3].eq(v["table"]).all()
            or v[["variable", "long_name"]].eq("").any().any()):
        raise ValueError("Invalid or duplicate ABS table/variable metadata")
    return t, v


def _validate_search(search: str | None) -> None:
    if search is not None:
        if not isinstance(search, str) or not search.strip():
            raise ValueError("search must be a nonblank string")


def _search_metadata(frame: pd.DataFrame, search: str | None) -> pd.DataFrame:
    _validate_search(search)
    if search is not None:
        mask = frame.apply(lambda col: col.astype("string").str.contains(
            search.strip(), case=False, regex=False)).any(axis=1)
        frame = frame.loc[mask]
    return frame.reset_index(drop=True)


def _available_table_ids(archive: ZipFile, geo: str, state: str, *,
                         year: int = 2021) -> list[str]:
    pattern = _vintage(year)["member_re"]
    ids = set()
    for member in archive.namelist():
        match = pattern.fullmatch(Path(member).name)
        if match and match.group(3).upper() == geo:
            member_state = match.group(2).upper()
            if _member_state(member_state, year) == state:
                ids.add(match.group(1)[:3].upper())
    if not ids:
        raise ValueError(f"Archive has no {year} {geo} tables for state {state}")
    return sorted(ids)


def list_census_tables(*, geo: str = "POA", state: str = "NSW", year: int = 2021,
                       source_zip: str | Path | None = None,
                       cache_dir: str | Path = "abs_cache",
                       search: str | None = None) -> pd.DataFrame:
    """List available logical tables with official names, populations and file parts."""
    geo, state = _validate_geography(geo, state, year=year)
    _validate_search(search)
    pattern = _vintage(year)["member_re"]
    source = _resolve_source(geo=geo, state=state, source_zip=source_zip, cache_dir=cache_dir,
                             year=year)
    with ZipFile(source) as archive:
        _check_archive(archive, year, geo=geo)
        tables, _ = _read_metadata(archive, year=year)
        available = []
        for table in _available_table_ids(archive, geo, state, year=year):
            paths = _find_members_validated(archive, table, geo, state, year=year)
            available.append((table, ",".join(pattern.fullmatch(Path(p).name).group(1).upper()
                                               for p in paths)))
        unknown = {table for table, _ in available} - set(tables["table"])
        if unknown:
            raise ValueError(f"Missing table metadata: {sorted(unknown)}")
        inventory = pd.DataFrame(available, columns=["table", "parts"])
        result = tables.merge(inventory, on="table", validate="one_to_one")
    return _search_metadata(result, search)


def describe_census(*, geo: str = "POA", state: str = "NSW", year: int = 2021,
                    tables: Sequence[str] | None = None,
                    source_zip: str | Path | None = None,
                    cache_dir: str | Path = "abs_cache",
                    search: str | None = None) -> pd.DataFrame:
    """Describe variables, all available tables by default; does not read data rows."""
    geo, state = _validate_geography(geo, state, year=year)
    requested = _normalize_tables(tables) if tables is not None else None
    _validate_search(search)
    pattern = _vintage(year)["member_re"]
    source = _resolve_source(geo=geo, state=state, source_zip=source_zip, cache_dir=cache_dir,
                             year=year)
    with ZipFile(source) as archive:
        _check_archive(archive, year, geo=geo)
        _, variables = _read_metadata(archive, year=year)
        if requested is None:
            requested = _available_table_ids(archive, geo, state, year=year)
        parts = set()
        for table in requested:
            paths = _find_members_validated(archive, table, geo, state, year=year)
            parts.update(pattern.fullmatch(Path(p).name).group(1).upper() for p in paths)
        result = variables.loc[variables["datapack_table"].isin(parts)].copy()
        missing = parts - set(result["datapack_table"])
        if missing:
            raise ValueError(f"Metadata missing for table parts: {sorted(missing)}")
    return _search_metadata(result, search)


_LOCK_NAME = ".abs-census-etl.lock"


@contextmanager
def _export_lock(out: Path):
    """Serialize cooperating exporters; keep the inode, release OS lock on process exit."""
    path = out / _LOCK_NAME
    if path.is_symlink():
        raise ValueError(f"Symlinked export lock is not supported: {path}")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except FileNotFoundError:
        try:
            fd = os.open(path, flags | os.O_CREAT | os.O_EXCL, 0o666)
        except FileExistsError:
            fd = os.open(path, flags)
    locked = False
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError(f"Export lock is not a regular file: {path}")
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
        except OSError as exc:
            busy = {errno.EAGAIN, errno.EWOULDBLOCK}
            if os.name == "nt":
                busy.add(errno.EACCES)
            if exc.errno in busy:
                raise BlockingIOError(exc.errno, f"Output folder is busy: {out}. "
                                      "Another export holds its lock; retry when it finishes. "
                                      "Do not delete the lock file.") from exc
            raise OSError(exc.errno, f"Cannot lock output folder {out}: {exc}. "
                          "Locking may be unsupported or unavailable on this filesystem; "
                          "use a local output folder. See docs/data-guide.md#concurrent-exports.") from exc
        yield
    finally:
        try:
            if locked:
                if os.name == "nt":
                    import msvcrt
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _source_hash(stream: BinaryIO) -> str:
    stream.seek(0)
    digest = hashlib.file_digest(stream, "sha256").hexdigest()
    stream.seek(0)
    return digest


def _writer_version() -> str:
    """Read the single version source in a checkout, installed metadata otherwise."""
    project = Path(__file__).with_name("pyproject.toml")
    try:
        with project.open("rb") as stream:
            metadata = tomllib.load(stream).get("project", {})
        if (isinstance(metadata, dict) and metadata.get("name") == "abs-census-etl"
                and isinstance(metadata.get("version"), str)):
            return metadata["version"]
    except (OSError, ValueError):
        pass
    try:
        installed = distribution("abs-census-etl")
        module = Path(__file__).resolve()
        if any(Path(installed.locate_file(file)).resolve() == module
               for file in installed.files or ()):
            return installed.version
    except (PackageNotFoundError, OSError, ValueError):
        pass
    return "unknown"


def _boundary_details(geo: str, year: int) -> dict:
    """Framework edition is not an individual administrative boundary date."""
    return {"framework": "ASGS", "edition": _vintage(year)["asgs_edition"],
            "structure": "main_structure" if geo in {"SA1", "SA2", "SA3", "SA4", "STE"}
                         else "non_abs_structure",
            "boundary_type": ("administrative" if geo in {"LGA", "SED", "CED", "STE"} else
                              "postal_approximation" if geo == "POA" else
                              "locality" if geo in {"SSC", "SAL"} else "statistical"),
            "geography": geo, "source_code_column": _code_column(geo, year),
            "reference_date": None}


def _source_revision(archive: ZipFile) -> dict:
    """Retain explicit change-document evidence without inferring a release date."""
    documents = []
    for member in sorted(archive.namelist()):
        if Path(member).name.casefold() != "summary_of_changes.txt":
            continue
        info = archive.getinfo(member)
        if info.file_size > 2 * 1024 * 1024:
            with archive.open(member) as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            documents.append({"member": member, "sha256": digest, "status": "oversize",
                              "encoding": None, "encoding_inferred": None, "text": None})
            continue
        content = archive.read(member)
        try:
            if content.startswith(b"\xef\xbb\xbf"):
                encoding, text = "utf-8-sig", content.decode("utf-8-sig")
            else:
                try:
                    encoding, text = "utf-8", content.decode("utf-8")
                    if content.isascii():
                        encoding = "ascii"
                except UnicodeDecodeError:
                    encoding, text = "cp1252", content.decode("cp1252")
        except UnicodeDecodeError:
            documents.append({"member": member, "sha256": hashlib.sha256(content).hexdigest(),
                              "status": "undecodable", "encoding": None,
                              "encoding_inferred": None, "text": None})
            continue
        documents.append({"member": member, "sha256": hashlib.sha256(content).hexdigest(),
                          "encoding": encoding, "encoding_inferred": encoding in {"cp1252", "utf-8"},
                          "text": text})
    status = ("documented" if any((d["text"] or "").strip() for d in documents) else
              "unavailable" if any(d.get("status") in {"oversize", "undecodable"}
                                   for d in documents) else "unknown")
    return {"status": status, "documents": documents}


def _reference_revision(revision: dict, documents: dict) -> dict:
    """Keep text once; table references retain facts if an old writer drops the store."""
    references = []
    for document in revision["documents"]:
        digest = document["sha256"]
        documents[digest] = {key: value for key, value in document.items()
                             if key not in {"member", "sha256"}}
        references.append({key: value for key, value in document.items() if key != "text"})
    return {"status": revision["status"], "documents": references}



def _document_metadata(document: dict) -> None:
    """Validate the common store/reference metadata without guessing encodings."""
    if not isinstance(document, dict):
        raise ValueError("Revision document must be an object")
    encoding = document.get("encoding")
    inferred = document.get("encoding_inferred")
    status = document.get("status")
    if status in ("oversize", "undecodable"):
        if encoding is not None or inferred is not None:
            raise ValueError("Unavailable encoding and inference must both be null")
    elif status not in (None, "documented") or encoding not in (
            "ascii", "utf-8", "utf-8-sig", "cp1252"):
        raise ValueError("Invalid revision document encoding or status")
    elif type(inferred) is not bool or inferred != (encoding in {"utf-8", "cp1252"}):
        raise ValueError("Invalid revision encoding inference")


def _validated_document_store(store: dict) -> dict:
    if not isinstance(store, dict):
        raise ValueError("source_documents must be an object")
    for digest, document in store.items():
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("Invalid revision document SHA-256")
        _document_metadata(document)
        text = document.get("text")
        if document.get("status") in ("oversize", "undecodable"):
            if text is not None:
                raise ValueError("Unavailable revision document must have null text")
        else:
            if not isinstance(text, str):
                raise ValueError("Revision document text must be a string")
            body = text.encode(document["encoding"])
            if hashlib.sha256(body).hexdigest() != digest:
                raise ValueError("Revision document SHA-256 mismatch")
    return dict(store)


def _retained_revision(revision: dict | None, documents: dict) -> dict | None:
    if revision is None:
        return None
    if (not isinstance(revision, dict)
            or revision.get("status") not in ("documented", "unknown", "unavailable")
            or not isinstance(revision.get("documents"), list)):
        raise ValueError("Invalid source_revision object/status/documents")
    references = []
    for reference in revision["documents"]:
        _document_metadata(reference)
        digest = reference.get("sha256")
        if (not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)
                or not isinstance(reference.get("member"), str) or not reference["member"]
                or "text" in reference
                or ("text_status" in reference and reference["text_status"] not in (
                    "available", "missing", "oversize", "undecodable"))):
            raise ValueError("Invalid revision reference; regenerate unreleased inline documents")
        document = documents.get(digest)
        if document is not None and any(reference.get(key) != document.get(key)
                                        for key in ("encoding", "encoding_inferred", "status")):
            raise ValueError("Revision reference disagrees with stored document")
        text_status = ("missing" if document is None else
                       document.get("status") if document.get("text") is None else "available")
        references.append({**reference, "text_status": text_status})
    available = any(ref["text_status"] == "available"
                    and documents[ref["sha256"]]["text"].strip() for ref in references)
    unavailable = any(ref["text_status"] != "available" for ref in references)
    return {**revision, "documents": references,
            "status": "documented" if available else "unavailable" if unavailable else "unknown"}


def _manifest_provenance_error(path: Path, error: Exception) -> ValueError:
    return ValueError(f"Invalid existing manifest: {path}: {error}. "
                      "Use a fresh output folder or remove only this manifest and "
                      "regenerate the inventory; exported data is not removed.")

def _preflight_format(out: Path, geo: str, state: str,
                      tables: Sequence[str], file_format: str, *, year: int = 2021) -> None:
    other_format = "parquet" if file_format == "csv" else "csv"
    conflicts = [out / f"{year}_GCP_{geo}_{state}_{table}_{shape}.{other_format}"
                 for table in tables for shape in ("wide", "long")
                 if (out / f"{year}_GCP_{geo}_{state}_{table}_{shape}.{other_format}").exists()]
    if conflicts:
        raise FileExistsError("Other-format exports already exist: "
                              + ", ".join(str(path) for path in conflicts) + ". "
                              "Use a fresh output directory or keep the existing format.")


def _preflight_destination(*, out_dir: Path, existing_parent: Path, geo: str,
                           state: str, table_ids: Sequence[str], long: bool,
                           file_format: str, warned_orphans: set[Path], year: int = 2021
                           ) -> tuple[Path, dict[str, Path], dict, dict]:
    """Inspect without writing; callers must repeat under lock before using results."""
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
    orphan_folders = [path for path in orphan_folders if path not in warned_orphans]
    if orphan_folders:
        # Never delete an orphan automatically: its provenance is unknown.
        warnings.warn("Staging folders without a recovery journal: "
                      + ", ".join(str(path) for path in orphan_folders)
                      + ". They hold unpublished files from an interrupted export or "
                      "one still running; delete them when no export is running.",
                      RuntimeWarning, stacklevel=3)
        warned_orphans.update(orphan_folders)
    _preflight_format(out_dir, geo, state, table_ids, file_format, year=year)
    manifest_path = out_dir / f"{year}_GCP_{geo}_{state}_manifest.json"
    targets = {table: out_dir / (f"{year}_GCP_{geo}_{state}_{table}_"
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
    retained_tables = {}
    source_documents = {}
    if manifest_path.exists():
        try:
            previous = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (ValueError, UnicodeError) as exc:
            raise ValueError(f"Invalid existing manifest: {manifest_path}. "
                             "Remove only this manifest or use a fresh output folder; "
                             "removing it discards prior inventory, not exported data.") from exc
        schema = previous.get("manifest_schema_version", 1) if isinstance(previous, dict) else 1
        if type(schema) is not int or schema != 1:
            raise ValueError(f"Unsupported manifest schema version {schema!r}: {manifest_path}; "
                             "use a compatible application or a fresh output folder")
        if (not isinstance(previous, dict)
                or previous.get("geography") != geo or previous.get("state") != state
                or previous.get("census_year") != year
                or previous.get("asgs_edition") != (_vintage(year)["asgs_edition"])
                or not isinstance(previous.get("tables"), dict)
                or any(not isinstance(entry, dict) or not isinstance(entry.get("file"), str)
                       for entry in previous["tables"].values())):
            raise ValueError(f"Incompatible existing manifest: {manifest_path}. "
                             "Remove only this manifest or use a fresh output folder; "
                             "removing it discards prior inventory, not exported data.")
        try:
            source_documents = _validated_document_store(previous.get("source_documents", {}))
            _retained_revision(previous.get("source_revision"), source_documents)
        except (ValueError, UnicodeError) as error:
            raise _manifest_provenance_error(manifest_path, error) from error
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
            # Derive identity from the validated inventory, never latest-call revision facts.
            identity = {"census_year": year, "profile": _vintage(year)["profile"],
                        "data_years": [year], "boundary": _boundary_details(geo, year)}
            for field, value in identity.items():
                if retained.get(field) is None:
                    retained[field] = value
                elif type(retained[field]) is not type(value) or retained[field] != value:
                    raise ValueError(f"Incompatible existing manifest: {manifest_path}: "
                                     f"table {table} contradicts {field}. Use a fresh output "
                                     "folder or regenerate this manifest's inventory.")
            retained.setdefault("source_revision", None)
            try:
                retained["source_revision"] = _retained_revision(
                    retained["source_revision"], source_documents)
            except (ValueError, UnicodeError) as error:
                raise _manifest_provenance_error(manifest_path, error) from error
            retained.setdefault("source_sha256", None)
            retained_tables[table] = retained
    return manifest_path, targets, retained_tables, source_documents


def export_census(*, geo: str = "POA", state: str = "NSW", year: int = 2021,
                  tables: Sequence[str] = ("G01",),
                  source_zip: str | Path | None = None,
                  cache_dir: str | Path = "abs_cache",
                  codes: Sequence[str | int | float] | None = None,
                  keep: Sequence[str] | None = None,
                  long: bool = False, out: str | Path = "abs_out",
                  file_format: str = "csv") -> dict[str, Path]:
    """Run extraction and write tables + a JSON manifest. Return output paths."""
    geo, state = _validate_geography(geo, state, year=year)
    year = int(year)  # Integral API inputs must serialize as ordinary JSON integers.
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
    if (file_format == "parquet" and find_spec("pyarrow") is None
            and find_spec("fastparquet") is None):
        raise ValueError(PARQUET_ENGINE_HELP)
    if not os.access(existing_parent, os.W_OK | os.X_OK):
        raise PermissionError(f"Read-only export output: {existing_parent}. "
                              "Make it writable or use a fresh output folder.")
    if source_zip is not None and not Path(source_zip).is_file():
        raise FileNotFoundError(f"Source ZIP is not a file: {source_zip}")
    warned_orphans: set[Path] = set()
    # Cooperating exports create the lock file before staging and never remove
    # it. With one present, lock first: a running export then reports busy and
    # its staging/journal state is never read unlocked. Without one, check an
    # explicit ZIP before creating it; _resolve_source rechecks under the lock.
    check_before_lock = (source_zip is not None
                         and not os.path.lexists(out_dir / _LOCK_NAME))
    if check_before_lock:
        # Reject known destination problems before opening an explicit ZIP.
        # Results are discarded here: only the locked reread feeds publication.
        _preflight_destination(
            out_dir=out_dir, existing_parent=existing_parent, geo=geo, state=state,
            table_ids=table_ids, long=long, file_format=file_format,
            warned_orphans=warned_orphans, year=year)
        _validate_zip(Path(source_zip))
        _validate_source_identity(Path(source_zip), year, geo=geo)
    out_dir.mkdir(parents=True, exist_ok=True)
    with _export_lock(out_dir):
        manifest_path, targets, retained_tables, source_documents = _preflight_destination(
            out_dir=out_dir, existing_parent=existing_parent, geo=geo, state=state,
            table_ids=table_ids, long=long, file_format=file_format,
            warned_orphans=warned_orphans, year=year)
        source = _resolve_source(geo=geo, state=state, source_zip=source_zip,
                                 cache_dir=cache_dir, year=year).resolve()
        with source.open("rb", buffering=0) as stream:
            source_sha256 = _source_hash(stream)
            data = _read_tables_from_zip(source=stream, geo=geo, state=state, tables=table_ids,
                                         codes=codes, keep=keep, long=long, year=year)
            with _zip_read_errors(source), ZipFile(stream) as archive:
                revision = _source_revision(archive)
            if _source_hash(stream) != source_sha256:
                raise ValueError(f"Source ZIP changed during export: {source}; retry with a stable copy")
        revision = _retained_revision(_reference_revision(revision, source_documents), source_documents)
        writer_version = _writer_version()
        url = datapack_url(geo, state, year=year)
        manifest = {"source": str(source), "source_url": url if source_zip is None else None,
                    "source_path_kind": "absolute", "file_format": file_format,
                    "manifest_scope": "latest_export_per_table", "source_sha256": source_sha256,
                    "census_year": year, "asgs_edition": _vintage(year)["asgs_edition"],
                    "manifest_schema_version": 1, "writer_version": writer_version,
                    "source_documents": source_documents, "profile": "GCP", "data_years": [year],
                    "boundary": _boundary_details(geo, year), "source_revision": revision,
                    "geography": geo,
                    "state": state, "created_utc": datetime.now(timezone.utc).isoformat(),
                    "tables": retained_tables}
        work = Path(tempfile.mkdtemp(dir=out_dir, prefix=".abs-export-"))
        staged = {}
        try:
            code = _code_column(geo, year)
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
                    "census_year": year, "writer_version": writer_version,
                    "profile": "GCP", "data_years": [year],
                    "boundary": manifest["boundary"], "source_revision": revision,
                    "file": target.name, "areas": n_areas,
                    "rows": len(df), "columns": df.columns.tolist(),
                    "source": str(source), "source_url": manifest["source_url"],
                    "source_path_kind": "absolute", "source_sha256": source_sha256,
                    "created_utc": manifest["created_utc"], "file_format": file_format,
                    "shape": "long" if long else "wide",
                }
            referenced = set()
            for entry in [manifest, *manifest["tables"].values()]:
                history = _retained_revision(entry.get("source_revision"), source_documents)
                entry["source_revision"] = history
                if isinstance(history, dict):
                    referenced.update(doc["sha256"] for doc in history.get("documents", [])
                                      if isinstance(doc, dict) and "sha256" in doc)
            manifest["source_documents"] = {digest: doc for digest, doc in source_documents.items()
                                            if digest in referenced}
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
    parser = args_parser()
    args = parser.parse_args(argv)
    if args.geo not in _vintage(args.year)["columns"]:
        parser.error(f"Unsupported geography {args.geo!r} for {args.year}; choose "
                     + ", ".join(sorted(_vintage(args.year)["columns"])))
    if args.search is not None and not (args.list_tables or args.describe):
        parser.error("--search requires --list-tables or --describe")
    if args.list_tables or args.describe:
        if args.codes is not None or args.keep is not None or args.long:
            parser.error("metadata discovery does not accept --codes, --keep or --long")
        if args.list_tables and args.tables is not None:
            parser.error("--list-tables lists every available table; use --search to filter")
        options = dict(geo=args.geo, state=args.state, year=args.year, source_zip=args.source_zip,
                       cache_dir=args.out / "raw", search=args.search)
        if args.describe:
            frame = describe_census(tables=args.tables, **options)
        else:
            frame = list_census_tables(**options)
        print(frame.to_csv(sep="\t", index=False), end="")
        return 0
    if args.list_columns:
        data = load_census_tables(geo=args.geo, state=args.state, year=args.year,
                                  tables=args.tables or ["G01"], source_zip=args.source_zip,
                                  cache_dir=args.out / "raw")
        code = _code_column(args.geo, args.year)
        for table, df in data.items():
            print(f"\n{table} [{args.geo}]: {len(df):,} areas, "
                  f"{len(df.columns) - 1} measures")
            print("\n".join(c for c in df.columns if c != code))
        return 0
    with warnings.catch_warnings(record=True) as caught:
        try:
            outputs = export_census(geo=args.geo, state=args.state, year=args.year, tables=args.tables or ["G01"],
                                    source_zip=args.source_zip,
                                    cache_dir=args.out / "raw", codes=args.codes,
                                    keep=args.keep, long=args.long, out=args.out,
                                    file_format=args.format)
        finally:
            for warning in caught:
                print(f"WARNING: {warning.message}", file=sys.stderr)
    for table, path in outputs.items():
        print(f"{table}: {path}")
    print(f"Manifest: {args.out / f'{args.year}_GCP_{args.geo}_{args.state}_manifest.json'}")
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
