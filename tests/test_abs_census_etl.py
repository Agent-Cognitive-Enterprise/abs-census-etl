"""Offline regression tests for abs_census_etl.py.

Run from anywhere:
    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from unittest.mock import patch
from zipfile import BadZipFile, ZipFile

import numpy as np
import pandas as pd

# Make the adjacent single-file module importable even when tests are launched
# from a different working directory.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
# Root ignores permission bits, so read-only refusal tests cannot run as root.
RUNNING_AS_ROOT = getattr(os, "geteuid", lambda: -1)() == 0

from abs_census_etl import (  # noqa: E402
    cli,
    combine_table,
    find_members,
    datapack_url,
    download_if_needed,
    export_census,
    load_census,
    load_census_tables,
    main,
)

POA_G01 = """POA_CODE_2021,Tot_P_P,Tot_P_M,Tot_P_F
POA2010,100,48,52
POA2000,400,..,205
"""
POA_G02 = """POA_CODE_2021,Median_age_persons
POA2010,31
POA2000,42
"""
# Real G04 field names; synthetic overlap exercises shared-column validation.
POA_G04A = """POA_CODE_2021,Age_yr_0_M,Age_yr_0_P
POA2010,100,11
POA2000,400,23
"""
POA_G04B = """POA_CODE_2021,Age_yr_0_M,Age_yr_56_P
POA2000,400,24
POA2010,100,12
"""
NT_BARE_G01 = """POA_CODE_2021,Tot_P_P,Tot_P_M,Tot_P_F
0800,100,48,52
0810,400,..,205
"""


def make_nt_zip(path: Path, *, bare: bool = False, spaced_header: bool = False) -> Path:
    csv = NT_BARE_G01 if bare else NT_BARE_G01.replace("0800", "POA0800").replace("0810", "POA0810")
    if spaced_header:
        csv = csv.replace("POA_CODE_2021", " POA_CODE_2021 ")
    with ZipFile(path, "w") as z:
        z.writestr("2021 Census GCP Postal Areas for NT/2021Census_G01_NT_POA.csv", csv)
    return path


SA1_G01 = """SA1_CODE_2021,Tot_P_P,Tot_P_M,Tot_P_F
10201102801,450,228,222
10201102802,410,200,210
"""


def make_zip(path: Path, *, poa_g01: str = POA_G01,
             poa_g04a: str = POA_G04A, poa_g04b: str = POA_G04B,
             extra: dict[str, str] | None = None) -> Path:
    try:
        from tests.test_data_features import workbook
    except ModuleNotFoundError:
        from test_data_features import workbook
    members = {
        "Metadata/Metadata_2021_GCP_DataPack_R1_R2.xlsx": workbook(),
        "Readme/2021AboutDataPacks_readme.txt": "This archive contains Census DataPack tables.",
        "unrelated.csv": "not,a,table\n",
        "2021 Census GCP Postal Areas for NSW/2021Census_G01_NSW_POA.csv": poa_g01,
        "2021 Census GCP Postal Areas for NSW/2021Census_G02_NSW_POA.csv": POA_G02,
        "2021 Census GCP Postal Areas for NSW/2021Census_G04A_NSW_POA.csv": poa_g04a,
        "2021 Census GCP Postal Areas for NSW/2021Census_G04B_NSW_POA.csv": poa_g04b,
        "2021 Census GCP Statistical Area 1 for NSW/2021Census_G01_NSW_SA1.csv": SA1_G01,
    }
    if extra:
        members.update(extra)
    with ZipFile(path, "w") as z:
        for member, csv in members.items():
            z.writestr(member, csv)
    return path


class CLIBoundaryTests(unittest.TestCase):
    def test_module_and_script_use_the_public_program_name(self):
        for command in ([sys.executable, '-m', 'abs_census_etl'],
                        [sys.executable, str(ROOT / 'abs_census_etl.py')]):
            for options in (['--help'], ['--unknown-option']):
                with self.subTest(command=command, options=options):
                    result = subprocess.run([*command, *options], cwd=ROOT,
                                            capture_output=True, text=True)
                    output = result.stdout + result.stderr
                    self.assertIn('usage: abs-census-etl ', output)
                    self.assertNotIn('usage: python ', output)
                    if options == ['--help']:
                        self.assertIn('    abs-census-etl --geo POA', output)
                        self.assertNotIn('python abs_census_etl.py', output)

    def test_cli_preserves_error_notes(self):
        error = OSError("publication failed")
        error.add_note("Keep the recovery journal")
        with patch("abs_census_etl.main", side_effect=error), \
                patch("sys.stderr", new_callable=io.StringIO) as stderr:
            self.assertEqual(cli(), 1)
        self.assertEqual(stderr.getvalue(),
                         "ERROR: publication failed\nKeep the recovery journal\n")

    def test_cli_reports_real_invalid_input(self):
        with patch("sys.stderr", new_callable=io.StringIO) as stderr:
            status = cli(["--geo", "POA", "--codes", "bad",
                          "--zip", "missing-offline.zip"])
        self.assertEqual(status, 1)
        self.assertIn("ERROR: Invalid POA", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())


class ABSModuleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.source = make_zip(self.base / "synthetic.zip")

    def test_load_census_returns_dataframe(self):
        df = load_census(source_zip=self.source, geo="POA", table="G01")
        self.assertIsInstance(df, pd.DataFrame)
        self.assertEqual(df.shape, (2, 4))
        self.assertEqual(df["POA_CODE_2021"].tolist(), ["POA2010", "POA2000"])
        self.assertEqual(df.loc[1, "Tot_P_P"], 400)
        self.assertTrue(pd.isna(df.loc[1, "Tot_P_M"]))

    def test_geography_codes_stay_strings_with_leading_zeroes(self):
        source = make_nt_zip(self.base / "nt_bare.zip", bare=True)
        df = load_census(source_zip=source, state="NT", codes=["0800"])
        self.assertEqual(df["POA_CODE_2021"].tolist(), ["0800"])
        self.assertTrue(pd.api.types.is_string_dtype(df["POA_CODE_2021"].dtype))

    def test_keep_subset_and_requested_column_order(self):
        df = load_census(source_zip=self.source, keep=["Tot_P_F", "Tot_P_P"])
        self.assertEqual(df.columns.tolist(),
                         ["POA_CODE_2021", "Tot_P_F", "Tot_P_P"])

    def test_split_table_joins_by_geography_not_row_position(self):
        df = load_census(source_zip=self.source, table="G04")
        self.assertEqual(df.columns.tolist(),
                         ["POA_CODE_2021", "Age_yr_0_M", "Age_yr_0_P", "Age_yr_56_P"])
        values = df.set_index("POA_CODE_2021")
        self.assertEqual(values.loc["POA2000", "Age_yr_56_P"], 24)
        self.assertEqual(values.loc["POA2010", "Age_yr_56_P"], 12)

    def test_request_split_part_directly(self):
        df = load_census(source_zip=self.source, table="G04A")
        self.assertEqual(df.columns.tolist(),
                         ["POA_CODE_2021", "Age_yr_0_M", "Age_yr_0_P"])

    def test_long_format_with_filtering(self):
        df = load_census(source_zip=self.source, table="G01", codes=["POA2000"],
                         keep=["Tot_P_P", "Tot_P_M"], long=True)
        self.assertEqual(df.columns.tolist(), ["POA_CODE_2021", "variable", "value"])
        self.assertEqual(len(df), 2)
        self.assertEqual(df["variable"].tolist(), ["Tot_P_P", "Tot_P_M"])
        self.assertTrue(pd.isna(df.loc[1, "value"]))

    def test_multiple_tables_returns_mapping_and_deduplicates_ids(self):
        result = load_census_tables(source_zip=self.source,
                                    tables=["g01", "G04", "G01"])
        self.assertEqual(list(result), ["G01", "G04"])
        self.assertEqual(result["G04"].shape, (2, 4))

    def test_sa1_census_codes(self):
        df = load_census(source_zip=self.source, geo="SA1", table="G01")
        self.assertEqual(df["SA1_CODE_2021"].tolist(),
                         ["10201102801", "10201102802"])
        self.assertEqual(df["Tot_P_P"].sum(), 860)

    def test_missing_codes_raise_instead_of_returning_empty_data(self):
        with self.assertRaisesRegex(ValueError, "Unmatched POA.*9999"):
            load_census(source_zip=self.source, codes=["9999"])

    def test_unknown_keep_column_raises(self):
        with self.assertRaisesRegex(ValueError, "Unknown column"):
            load_census(source_zip=self.source, keep=["DOES_NOT_EXIST"])

    def test_unknown_table_raises(self):
        with self.assertRaisesRegex(ValueError, "Table G99 not found"):
            load_census(source_zip=self.source, table="G99")

    def test_rejects_invalid_geography_table_and_state(self):
        with self.assertRaisesRegex(ValueError, "Unsupported geography"):
            load_census(source_zip=self.source, geo="MB")
        with self.assertRaisesRegex(ValueError, "Unsupported state"):
            load_census(source_zip=self.source, state="BAD")
        with self.assertRaisesRegex(ValueError, "Invalid table ID"):
            load_census(source_zip=self.source, table="G1")
        with self.assertRaisesRegex(ValueError, "sequence"):
            load_census_tables(source_zip=self.source, tables="G01")

    def test_absence_of_source_zip_never_silently_downloads(self):
        with patch("abs_census_etl.download_if_needed", side_effect=AssertionError("network")):
            with self.assertRaises(FileNotFoundError):
                load_census(source_zip=self.base / "missing.zip")

    def test_missing_code_field_raises_clear_error(self):
        source = make_zip(self.base / "badheader.zip",
                          poa_g01="something_else,Tot_P_P\nPOA2010,100\n")
        with self.assertRaisesRegex(ValueError, "has no POA_CODE_2021"):
            load_census(source_zip=source)

    def test_duplicate_geographic_rows_rejected(self):
        source = make_zip(self.base / "duplicate.zip",
                          poa_g01="POA_CODE_2021,Tot_P_P\nPOA2010,100\nPOA2010,110\n")
        with self.assertRaisesRegex(ValueError, "Missing or repeated"):
            load_census(source_zip=source)

    def test_split_column_conflict_rejected(self):
        source = make_zip(self.base / "conflict.zip",
                          poa_g04b=POA_G04B.replace("POA2000,400,24", "POA2000,405,24"))
        with self.assertRaisesRegex(ValueError, "Conflicting repeated column"):
            load_census(source_zip=source, table="G04")

    def test_split_code_mismatch_rejected(self):
        source = make_zip(self.base / "mismatch.zip",
                          poa_g04b=POA_G04B.replace("POA2010,100,12", "1111,100,12"))
        with self.assertRaisesRegex(ValueError, "different POA_CODE_2021"):
            load_census(source_zip=source, table="G04")

    def test_whitespace_in_raw_code_column_still_preserves_leading_zeroes(self):
        source = make_nt_zip(self.base / "nt_whitespace.zip", bare=True, spaced_header=True)
        df = load_census(source_zip=source, state="NT")
        self.assertEqual(df["POA_CODE_2021"].tolist(), ["0800", "0810"])

    def test_csv_export_manifest(self):
        out = self.base / "output"
        files = export_census(source_zip=self.source, tables=["G01", "G04"],
                              out=out, file_format="csv", keep=None)
        self.assertEqual(list(files), ["G01", "G04"])
        self.assertTrue(all(p.is_file() for p in files.values()))
        manifest = json.loads((out / "2021_GCP_POA_NSW_manifest.json").read_text())
        self.assertEqual(manifest["tables"]["G01"]["areas"], 2)
        self.assertEqual(manifest["tables"]["G04"]["rows"], 2)
        self.assertIsNone(manifest["source_url"])
        # The exported CSV must retain official ABS identifiers
        round_trip = pd.read_csv(files["G01"], dtype={"POA_CODE_2021": "string"})
        self.assertEqual(round_trip["POA_CODE_2021"].tolist(), ["POA2010", "POA2000"])

    def test_long_export_manifest_counts_unique_geographies(self):
        out = self.base / "long_out"
        export_census(source_zip=self.source, tables=["G01"], out=out,
                      file_format="csv", long=True)
        manifest = json.loads((out / "2021_GCP_POA_NSW_manifest.json").read_text())
        self.assertEqual(manifest["tables"]["G01"]["areas"], 2)
        self.assertEqual(manifest["tables"]["G01"]["rows"], 6)

    def test_export_invalid_format(self):
        with self.assertRaisesRegex(ValueError, "file_format"):
            export_census(source_zip=self.source, file_format="xls")

    def test_local_zip_does_not_call_network(self):
        with patch("abs_census_etl.requests.get", side_effect=AssertionError("network called")):
            self.assertEqual(len(load_census(source_zip=self.source)), 2)
            export_census(source_zip=self.source, out=self.base / "offline",
                          file_format="csv")

    def test_cache_skips_download(self):
        cached = self.base / "cached.zip"
        cached.write_bytes(self.source.read_bytes())
        with patch("abs_census_etl.requests.get", side_effect=AssertionError("network called")):
            download_if_needed("https://example.org/a.zip", cached)
        self.assertEqual(cached.read_bytes(), self.source.read_bytes())

    def test_bad_zip_raises(self):
        corrupted = self.base / "bad.zip"
        corrupted.write_text("not a zip")
        with self.assertRaises(BadZipFile):
            load_census(source_zip=corrupted)

    def test_cli_main_writes_using_same_api(self):
        out = self.base / "cli"
        code = main(["--geo", "POA", "--state", "NSW", "--zip", str(self.source),
                     "--tables", "G01", "--codes", "POA2010", "--keep", "Tot_P_P",
                     "--format", "csv", "--out", str(out)])
        self.assertEqual(code, 0)
        df = pd.read_csv(out / "2021_GCP_POA_NSW_G01_wide.csv",
                         dtype={"POA_CODE_2021": "string"})
        self.assertEqual(df["POA_CODE_2021"].tolist(), ["POA2010"])
        self.assertEqual(df.columns.tolist(), ["POA_CODE_2021", "Tot_P_P"])

    def test_cli_list_columns_does_not_write_output(self):
        out = self.base / "list"
        with patch("sys.stdout", new_callable=io.StringIO) as output:
            code = main(["--geo", "SA1", "--zip", str(self.source),
                         "--tables", "G01", "--list-columns", "--out", str(out)])
        self.assertEqual(code, 0)
        self.assertIn("Tot_P_P", output.getvalue())
        self.assertFalse(out.exists())

    def test_import_has_no_side_effects(self):
        empty = self.base / "import_test"
        empty.mkdir()
        env = dict(os.environ, PYTHONPATH=str(ROOT))
        p = subprocess.run([sys.executable, "-c", "import abs_census_etl"],
                           cwd=empty, capture_output=True, text=True, env=env,
                           check=False)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(list(empty.iterdir()), [])

    def test_optional_parquet_roundtrip(self):
        try:
            import pyarrow  # noqa: F401
        except ImportError:
            self.skipTest("pyarrow is not installed")
        source = make_nt_zip(self.base / "nt_parquet.zip", bare=True)
        files = export_census(source_zip=source, state="NT", out=self.base / "parquet_out",
                              file_format="parquet")
        restored = pd.read_parquet(files["G01"])
        self.assertEqual(restored["POA_CODE_2021"].tolist(), ["0800", "0810"])

    def test_fresh_download_is_validated_and_cached(self):
        destination = self.base / "downloads" / "live.zip"
        fixture_bytes = self.source.read_bytes()

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def raise_for_status(self):
                pass

            def iter_content(self, chunk_size):
                yield fixture_bytes[:32]
                yield b""
                yield fixture_bytes[32:]

        with patch("abs_census_etl.requests.get", return_value=FakeResponse()) as get:
            download_if_needed("https://example.org/synthetic.zip", destination)
            get.assert_called_once()
            self.assertEqual(get.call_args.kwargs["headers"]["User-Agent"],
                             "abs-census-etl")
        self.assertEqual(destination.read_bytes(), fixture_bytes)
        self.assertEqual(list(destination.parent.glob("*.part")), [])

    def test_bad_download_does_not_leave_corrupt_cache(self):
        destination = self.base / "downloads" / "bad.zip"

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def raise_for_status(self):
                pass

            def iter_content(self, chunk_size):
                yield b"this is not a ZIP"

        with patch("abs_census_etl.requests.get", return_value=FakeResponse()):
            with self.assertRaises(BadZipFile):
                download_if_needed("https://example.org/invalid.zip", destination)
        self.assertFalse(destination.exists())
        self.assertEqual(list(destination.parent.glob("*.part")), [])

    def test_cli_subprocess_still_functions(self):
        out = self.base / "subprocess"
        completed = subprocess.run(
            [sys.executable, str(ROOT / "abs_census_etl.py"),
             "--geo", "SA1", "--state", "NSW", "--zip", str(self.source),
             "--tables", "G01", "--format", "csv", "--out", str(out)],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertTrue((out / "2021_GCP_SA1_NSW_G01_wide.csv").is_file())

    def test_poa_filter_accepts_bare_and_prefixed_codes(self):
        for requested, expected in [("2000", "POA2000"), ("POA2000", "POA2000"),
                                    ("2010", "POA2010"), ("POA2010", "POA2010")]:
            with self.subTest(requested=requested):
                df = load_census(source_zip=self.source, codes=[requested])
                self.assertEqual(df["POA_CODE_2021"].tolist(), [expected])

    def test_poa_filter_preserves_legacy_bare_source_codes(self):
        source = make_nt_zip(self.base / "bare.zip", bare=True)
        df = load_census(source_zip=source, state="NT", codes=["POA0800"])
        self.assertEqual(df["POA_CODE_2021"].tolist(), ["0800"])

    def test_codes_rejects_scalar_string(self):
        with self.assertRaisesRegex(ValueError, "codes must be a sequence"):
            load_census(source_zip=self.source, codes="2000")

    def test_sa1_filter_retains_identifier_format(self):
        df = load_census(source_zip=self.source, geo="SA1", codes=["10201102801"])
        self.assertEqual(df["SA1_CODE_2021"].tolist(), ["10201102801"])

    def test_documented_population_and_median_age_fields(self):
        population = load_census(source_zip=self.source, table="G01", codes=["2000"],
                                 keep=["Tot_P_P"])
        median = load_census(source_zip=self.source, table="G02", codes=["2000"],
                             keep=["Median_age_persons"])
        self.assertEqual(population["Tot_P_P"].tolist(), [400])
        self.assertEqual(median["Median_age_persons"].tolist(), [42])
        with self.assertRaisesRegex(ValueError, "Unknown column"):
            load_census(source_zip=self.source, table="G01", keep=["Median_age_P"])

    def test_wrong_state_export_creates_only_coordination_file(self):
        out = self.base / "wrong_state"
        with self.assertRaisesRegex(ValueError, "state VIC"):
            export_census(source_zip=self.source, state="VIC", out=out)
        self.assertEqual([p.name for p in out.iterdir()], [".abs-census-etl.lock"])

    def test_mixed_state_zip_selects_requested_state(self):
        source = make_zip(self.base / "mixed.zip", extra={
            "2021Census_G01_VIC_POA.csv": "POA_CODE_2021,Tot_P_P\nPOA3000,99\n",
            "2021Census_G04_VIC_POA.csv": "POA_CODE_2021,Age_yr_0_P\nPOA3000,5\n",
        })
        df = load_census(source_zip=source, state="VIC")
        self.assertEqual(df["POA_CODE_2021"].tolist(), ["POA3000"])
        # Another state's full table must not shadow this state's split table.
        split = load_census(source_zip=source, state="NSW", table="G04")
        self.assertEqual(split.shape, (2, 4))
        self.assertIn("Age_yr_56_P", split)

    def test_split_parts_from_different_states_are_not_combined(self):
        source = self.base / "mixed_parts.zip"
        with ZipFile(source, "w") as z:
            z.writestr("2021Census_G04A_NSW_POA.csv", POA_G04A)
            z.writestr("2021Census_G04B_VIC_POA.csv", POA_G04B)
        with self.assertRaisesRegex(ValueError, "missing for state NSW"):
            load_census(source_zip=source, table="G04")

    def test_national_archive_uses_aust_member_token(self):
        source = self.base / "national.zip"
        with ZipFile(source, "w") as z:
            z.writestr("2021Census_G01_AUST_POA.csv", POA_G01)
        df = load_census(source_zip=source, state="AUS")
        self.assertEqual(len(df), 2)
        with self.assertRaisesRegex(ValueError, "state NSW"):
            load_census(source_zip=source, state="NSW")
        with self.assertRaisesRegex(ValueError, "state AUS"):
            load_census(source_zip=self.source, state="AUS")

    def test_direct_combine_table_still_works_without_state(self):
        with ZipFile(self.source) as z:
            self.assertEqual(combine_table(z, "G04", "POA").shape, (2, 4))

    def test_split_equivalent_numeric_values_and_missingness(self):
        for left, right in [("100", "100.0"), ("..", "n.p."),
                            ("100.5", "100.50")]:
            with self.subTest(left=left, right=right):
                source = make_zip(self.base / "equivalent.zip",
                    poa_g04a=POA_G04A.replace("POA2010,100,11", f"POA2010,{left},11"),
                    poa_g04b=POA_G04B.replace("POA2010,100,12", f"POA2010,{right},12"))
                self.assertEqual(load_census(source_zip=source, table="G04").shape, (2, 4))

    def test_split_different_numeric_values_and_missingness_rejected(self):
        for left, right in [("100", "100.5"), ("..", "100"), ("100", "..")]:
            with self.subTest(left=left, right=right):
                source = make_zip(self.base / "different.zip",
                    poa_g04a=POA_G04A.replace("POA2010,100,11", f"POA2010,{left},11"),
                    poa_g04b=POA_G04B.replace("POA2010,100,12", f"POA2010,{right},12"))
                with self.assertRaisesRegex(ValueError, "Conflicting repeated column"):
                    load_census(source_zip=source, table="G04")

    def test_api_defaults_to_csv_without_parquet_engine(self):
        with patch.object(pd.DataFrame, "to_parquet", side_effect=AssertionError("parquet")):
            files = export_census(source_zip=self.source, out=self.base / "default")
        self.assertEqual(files["G01"].suffix, ".csv")
        self.assertTrue(files["G01"].is_file())

    def test_cli_defaults_to_csv_and_accepts_bare_poa_code(self):
        out = self.base / "default_cli"
        with patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(main(["--geo", "POA", "--zip", str(self.source),
                                   "--codes", "2000", "--out", str(out)]), 0)
        df = pd.read_csv(out / "2021_GCP_POA_NSW_G01_wide.csv")
        self.assertEqual(df["POA_CODE_2021"].tolist(), ["POA2000"])

    def test_nt_numeric_and_prefixed_inputs_normalize_without_changing_output(self):
        source = make_nt_zip(self.base / "nt.zip")
        for code in [800, "800", "0800", "POA0800", "poa0800", " PoA0800 ", " 0800 "]:
            with self.subTest(code=code):
                df = load_census(source_zip=source, state="NT", codes=[code])
                self.assertEqual(df["POA_CODE_2021"].tolist(), ["POA0800"])
        df = load_census(source_zip=source, state="NT", codes=[800, "0800", "POA0800"])
        self.assertEqual(len(df), 1)

    def test_nsw_filter_input_case_and_whitespace(self):
        for code in [" 2000", "2000 ", "poa2000", " PoA2000 "]:
            with self.subTest(code=code):
                df = load_census(source_zip=self.source, codes=[code])
                self.assertEqual(df["POA_CODE_2021"].tolist(), ["POA2000"])

    def test_sa1_filter_trims_input_without_padding(self):
        df = load_census(source_zip=self.source, geo="SA1", codes=[" 10201102801 "])
        self.assertEqual(df["SA1_CODE_2021"].tolist(), ["10201102801"])
        with self.assertRaisesRegex(ValueError, "Unmatched SA1"):
            load_census(source_zip=self.source, geo="SA1", codes=[800])

    def test_all_or_partially_missing_inputs_fail_before_export(self):
        for codes in [[200], ["2000", "2001"], ["9999", "2001"], [""], []]:
            with self.subTest(codes=codes):
                out = self.base / "missing_out"
                with self.assertRaises(ValueError) as caught:
                    export_census(source_zip=self.source, codes=codes, out=out)
                if "2001" in codes:
                    self.assertIn("2001", str(caught.exception))
                if "9999" in codes:
                    self.assertIn("9999", str(caught.exception))
                self.assertEqual([p.name for p in out.iterdir()], [".abs-census-etl.lock"])

    def test_unmatched_nt_input_is_not_silently_accepted_after_padding(self):
        source = make_nt_zip(self.base / "nt.zip")
        with self.assertRaisesRegex(ValueError, "Unmatched POA.*200"):
            load_census(source_zip=source, state="NT", codes=[200])

    def test_cli_unmatched_codes_exit_nonzero_without_output(self):
        out = self.base / "bad_cli"
        p = subprocess.run([sys.executable, str(ROOT / "abs_census_etl.py"),
                            "--geo", "POA", "--zip", str(self.source),
                            "--codes", "2000", "2001", "--out", str(out)],
                           text=True, capture_output=True)
        self.assertEqual(p.returncode, 1)
        self.assertIn("2001", p.stderr)
        self.assertEqual([p.name for p in out.iterdir()], [".abs-census-etl.lock"])

    def test_csv_roundtrip_preserves_bare_nt_leading_zeroes(self):
        source = make_nt_zip(self.base / "nt_bare.zip", bare=True)
        files = export_census(source_zip=source, state="NT", out=self.base / "nt_csv")
        restored = pd.read_csv(files["G01"], dtype={"POA_CODE_2021": "string"})
        self.assertEqual(restored["POA_CODE_2021"].tolist(), ["0800", "0810"])
        self.assertIn("0800,", files["G01"].read_text())

    def test_other_format_collision_preflights_all_tables_and_preserves_files(self):
        for new_format, old_format in [("csv", "parquet"), ("parquet", "csv")]:
            for new_long, old_shape in [(False, "wide"), (False, "long"),
                                         (True, "wide"), (True, "long")]:
                with self.subTest(new_format=new_format, new_long=new_long, old_shape=old_shape):
                    out = self.base / f"collision_{new_format}_{new_long}_{old_shape}"
                    out.mkdir()
                    # Conflict in the LAST requested table must prevent G01 writes too.
                    old = out / f"2021_GCP_POA_NSW_G04_{old_shape}.{old_format}"
                    old.write_bytes(b"keep old data")
                    manifest = out / "2021_GCP_POA_NSW_manifest.json"
                    manifest.write_text(json.dumps({"source": "old.zip", "source_url": None,
                        "created_utc": "previous run", "geography": "POA", "state": "NSW",
                        "census_year": 2021, "asgs_edition": 3, "tables": {}}))
                    before = {p.name: p.read_bytes() for p in out.iterdir()}
                    with self.assertRaisesRegex(FileExistsError, "fresh output directory"):
                        export_census(source_zip=self.source, tables=["G01", "G04"],
                                      out=out, file_format=new_format, long=new_long)
                    self.assertEqual({p.name: p.read_bytes() for p in out.iterdir()}, before)

    def test_same_format_rerun_updates_requested_files(self):
        out = self.base / "rerun"
        export_census(source_zip=self.source, out=out)
        files = export_census(source_zip=self.source, codes=["2000"], out=out)
        self.assertEqual(pd.read_csv(files["G01"])["POA_CODE_2021"].tolist(), ["POA2000"])
        manifest = json.loads((out / "2021_GCP_POA_NSW_manifest.json").read_text())
        self.assertEqual(manifest["file_format"], "csv")

    def test_direct_helpers_reject_mixed_states_even_with_identical_codes(self):
        for full in [False, True]:
            source = self.base / f"ambiguous_{full}.zip"
            with ZipFile(source, "w") as z:
                if full:
                    z.writestr("2021Census_G04_NSW_POA.csv", POA_G04A)
                    z.writestr("2021Census_G04_VIC_POA.csv", POA_G04A)
                else:
                    z.writestr("2021Census_G04A_NSW_POA.csv", POA_G04A)
                    z.writestr("2021Census_G04B_VIC_POA.csv", POA_G04B)
            with ZipFile(source) as z:
                for helper in [find_members, combine_table]:
                    with self.subTest(full=full, helper=helper.__name__):
                        with self.assertRaisesRegex(ValueError, "Ambiguous state"):
                            helper(z, "G04", "POA")
                if full:
                    self.assertEqual(combine_table(z, "G04", "POA", "NSW").shape, (2, 3))

    def test_direct_state_inference_ignores_unrelated_tables(self):
        source = make_zip(self.base / "unrelated.zip", extra={
            "2021Census_G99_VIC_POA.csv": "POA_CODE_2021,Other\nPOA3000,5\n",
        })
        with ZipFile(source) as z:
            self.assertEqual(combine_table(z, "G04", "POA").shape, (2, 4))

    def test_national_sa1_archive_infers_aust_and_selects_aus(self):
        source = self.base / "national_sa1.zip"
        with ZipFile(source, "w") as z:
            z.writestr("2021Census_G01_AUST_SA1.csv", SA1_G01)
        self.assertEqual(len(load_census(source_zip=source, geo="SA1", state="AUS")), 2)
        with ZipFile(source) as z:
            self.assertEqual(len(combine_table(z, "G01", "SA1")), 2)

    def test_format_conflict_is_reported_before_any_source_access(self):
        out = self.base / "early_conflict"
        out.mkdir()
        existing = out / "2021_GCP_POA_NSW_G01_wide.parquet"
        existing.write_bytes(b"existing data")
        invalid_archive = self.base / "not_an_archive.zip"
        invalid_archive.write_bytes(b"not a ZIP: format refusal must precede opening it")
        with patch("abs_census_etl._resolve_source", side_effect=AssertionError("source accessed")):
            for source in [None, invalid_archive]:
                with self.subTest(source=source):
                    with self.assertRaises(FileExistsError) as caught:
                        export_census(source_zip=source, out=out)
                    self.assertIn(str(existing), str(caught.exception))
                    self.assertNotIn("PosixPath(", str(caught.exception))
            with patch("sys.stdout", new_callable=io.StringIO):
                with self.assertRaises(FileExistsError):
                    main(["--geo", "POA", "--out", str(out)])
        self.assertEqual(set(out.iterdir()), {existing, out / ".abs-census-etl.lock"})

    def test_poa_whole_number_floats_from_spreadsheets(self):
        source = make_nt_zip(self.base / "float_nt.zip")
        spreadsheet = pd.Series([800, None, 810])  # pandas infers float dtype
        for code, expected in zip(spreadsheet.dropna().tolist(), ["POA0800", "POA0810"]):
            df = load_census(source_zip=source, state="NT", codes=[code])
            self.assertEqual(df["POA_CODE_2021"].tolist(), [expected])
        df = load_census(source_zip=self.source, codes=[2000.0])
        self.assertEqual(df["POA_CODE_2021"].tolist(), ["POA2000"])

    def test_poa_fractional_nonfinite_boolean_and_blank_inputs_fail(self):
        for code in [800.5, float("nan"), float("inf"), -float("inf"), True, False, "", "800.0"]:
            with self.subTest(code=code):
                with self.assertRaises(ValueError):
                    load_census(source_zip=self.source, codes=[code])
        with self.assertRaisesRegex(ValueError, "Invalid SA1"):
            load_census(source_zip=self.source, geo="SA1", codes=[10201102801.0])

    def test_manifest_keeps_tables_across_formats_and_source_archives(self):
        try:
            import pyarrow  # noqa: F401
        except ImportError:
            self.skipTest("pyarrow is not installed")
        out = self.base / "cumulative"
        first = export_census(source_zip=self.source, out=out, file_format="parquet",
                               codes=["2000"])
        path = out / "2021_GCP_POA_NSW_manifest.json"
        old_entry = json.loads(path.read_text())["tables"]["G01"]
        original_bytes = first["G01"].read_bytes()
        other_source = make_zip(self.base / "other.zip")
        export_census(source_zip=other_source, tables=["G02"], out=out, long=True)
        manifest = json.loads(path.read_text())
        self.assertEqual(set(manifest["tables"]), {"G01", "G02"})
        self.assertEqual(manifest["tables"]["G01"], old_entry)
        self.assertEqual(first["G01"].read_bytes(), original_bytes)
        self.assertEqual(old_entry["file_format"], "parquet")
        self.assertEqual(old_entry["shape"], "wide")
        self.assertEqual(old_entry["areas"], 1)
        self.assertEqual(old_entry["source"], str(self.source.resolve()))
        self.assertEqual(manifest["tables"]["G02"]["source"], str(other_source.resolve()))
        self.assertEqual(manifest["tables"]["G02"]["file_format"], "csv")
        self.assertEqual(manifest["tables"]["G02"]["shape"], "long")
        self.assertEqual(manifest["source"], str(other_source.resolve()))
        self.assertEqual(manifest["manifest_scope"], "latest_export_per_table")
        before_failure = {p.name: p.read_bytes() for p in out.iterdir()}
        with self.assertRaises(ValueError):
            export_census(source_zip=self.source, tables=["G02"], codes=["9999"], out=out)
        self.assertEqual({p.name: p.read_bytes() for p in out.iterdir()}, before_failure)

    def test_existing_manifest_must_be_valid_before_source_access(self):
        out = self.base / "invalid_manifest"
        out.mkdir()
        path = out / "2021_GCP_POA_NSW_manifest.json"
        for content in ['bad json', '[]', '{"tables": []}',
                        json.dumps({"geography": "POA", "state": "VIC", "tables": {}}),
                        json.dumps({"geography": "POA", "state": "NSW", "census_year": 2021,
                                    "asgs_edition": 3, "tables": {"G01": {}}})]:
            with self.subTest(content=content):
                path.write_text(content)
                with patch("abs_census_etl._resolve_source", side_effect=AssertionError("source accessed")):
                    with self.assertRaises(ValueError):
                        export_census(out=out)
                self.assertEqual(path.read_text(), content)
                self.assertEqual(set(out.iterdir()), {path, out / ".abs-census-etl.lock"})

    def test_manifest_backfills_provenance_from_older_schema(self):
        out = self.base / "old_schema"
        export_census(source_zip=self.source, out=out)
        path = out / "2021_GCP_POA_NSW_manifest.json"
        old = json.loads(path.read_text())
        for field in ["source", "source_url", "created_utc", "file_format", "shape"]:
            del old["tables"]["G01"][field]
        del old["file_format"]
        path.write_text(json.dumps(old))
        export_census(source_zip=self.source, tables=["G02"], out=out)
        entry = json.loads(path.read_text())["tables"]["G01"]
        self.assertEqual(entry["source"], old["source"])
        self.assertEqual(entry["created_utc"], old["created_utc"])
        self.assertEqual(entry["file_format"], "csv")
        self.assertEqual(entry["shape"], "wide")

    def test_manifest_reexport_replaces_only_requested_table_entry(self):
        out = self.base / "replace_manifest"
        export_census(source_zip=self.source, tables=["G01", "G02"], out=out)
        path = out / "2021_GCP_POA_NSW_manifest.json"
        old = json.loads(path.read_text())
        export_census(source_zip=self.source, codes=["2000"], long=True, out=out)
        current = json.loads(path.read_text())
        self.assertEqual(current["tables"]["G02"], old["tables"]["G02"])
        self.assertEqual(current["tables"]["G01"]["shape"], "long")
        self.assertEqual(current["tables"]["G01"]["areas"], 1)
        self.assertTrue((out / old["tables"]["G01"]["file"]).exists())

    def test_direct_full_table_ambiguity_requires_explicit_state(self):
        source = self.base / "ambiguous_full.zip"
        with ZipFile(source, "w") as z:
            z.writestr("2021Census_G01_NSW_POA.csv", POA_G01)
            z.writestr("2021Census_G01_VIC_POA.csv", POA_G01)
        with ZipFile(source) as z:
            for helper in [find_members, combine_table]:
                with self.subTest(helper=helper.__name__):
                    with self.assertRaisesRegex(ValueError, "Ambiguous state"):
                        helper(z, "G01", "POA")

    def test_archive_metadata_readme_and_unrelated_csv_files_are_ignored(self):
        with ZipFile(self.source) as z:
            self.assertIn("Metadata/Metadata_2021_GCP_DataPack_R1_R2.xlsx", z.namelist())
            self.assertIn("Readme/2021AboutDataPacks_readme.txt", z.namelist())
            members = find_members(z, "G01", "POA", "NSW")
            self.assertEqual(members, ["2021 Census GCP Postal Areas for NSW/2021Census_G01_NSW_POA.csv"])
        self.assertEqual(load_census(source_zip=self.source).shape, (2, 4))

    def test_mutation_runner_reports_missing_dependencies_as_setup_error(self):
        run = subprocess.run([sys.executable, "-S", str(ROOT / "tests/check_mutations.py")],
                             capture_output=True, text=True)
        self.assertEqual(run.returncode, 2)
        self.assertIn("Mutation setup failed", run.stderr)
        self.assertIn(".venv", run.stderr)
        self.assertNotRegex(run.stdout, r"(?m)^\d+/\d+ mutations caught")

    def test_manifest_interrupted_partial_write_preserves_old_json_and_recovers(self):
        out = self.base / "interrupt"
        export_census(source_zip=self.source, out=out)
        manifest = out / "2021_GCP_POA_NSW_manifest.json"
        before = manifest.read_bytes()
        for failure in [OSError("disk full"), KeyboardInterrupt()]:
            with self.subTest(failure=type(failure).__name__):
                def interrupted(data, stream, **kwargs):
                    stream.write('{"tables":')
                    raise failure
                with patch("abs_census_etl.json.dump", side_effect=interrupted):
                    with self.assertRaises(type(failure)):
                        export_census(source_zip=self.source, tables=["G02"], out=out)
                self.assertEqual(manifest.read_bytes(), before)
                self.assertEqual(set(json.loads(manifest.read_text())["tables"]), {"G01"})
                self.assertEqual(list(out.rglob(".*.tmp")), [])
        export_census(source_zip=self.source, tables=["G02"], out=out)
        self.assertEqual(set(json.loads(manifest.read_text())["tables"]), {"G01", "G02"})

    def test_manifest_replace_failure_preserves_old_json_and_removes_temp_file(self):
        out = self.base / "rename_failure"
        export_census(source_zip=self.source, out=out)
        manifest = out / "2021_GCP_POA_NSW_manifest.json"
        before = manifest.read_bytes()
        with patch.object(Path, "replace", side_effect=OSError("rename failed")):
            with self.assertRaisesRegex(OSError, "rename failed"):
                export_census(source_zip=self.source, tables=["G02"], out=out)
        self.assertEqual(manifest.read_bytes(), before)
        self.assertEqual(list(out.rglob(".*.tmp")), [])
        export_census(source_zip=self.source, tables=["G02"], out=out)
        self.assertIn("G02", json.loads(manifest.read_text())["tables"])

    def test_failed_first_manifest_write_leaves_no_partial_manifest(self):
        out = self.base / "first_failure"
        with patch("abs_census_etl.json.dump", side_effect=KeyboardInterrupt()):
            with self.assertRaises(KeyboardInterrupt):
                export_census(source_zip=self.source, out=out)
        self.assertFalse((out / "2021_GCP_POA_NSW_manifest.json").exists())
        self.assertEqual(list(out.rglob(".*.tmp")), [])
        export_census(source_zip=self.source, out=out)
        self.assertIn("G01", json.loads((out / "2021_GCP_POA_NSW_manifest.json").read_text())["tables"])

    def test_truncated_manifest_error_explains_recovery_without_deleting_data(self):
        out = self.base / "recover"
        paths = export_census(source_zip=self.source, out=out)
        path = out / "2021_GCP_POA_NSW_manifest.json"
        path.write_text(path.read_text()[:30])
        data_before = paths["G01"].read_bytes()
        with patch("abs_census_etl._resolve_source", side_effect=AssertionError("source accessed")):
            with self.assertRaisesRegex(ValueError, "Remove only this manifest or use a fresh output folder"):
                export_census(source_zip=self.source, out=out)
        self.assertEqual(paths["G01"].read_bytes(), data_before)
        path.unlink()  # Explicit recovery selected by the user, never automatic.
        export_census(source_zip=self.source, out=out)
        self.assertIn("G01", json.loads(path.read_text())["tables"])

    def test_manifest_paths_resolve_from_its_directory_across_cwd_changes(self):
        work = self.base / "work"
        (work / "a").mkdir(parents=True)
        original_cwd = Path.cwd()
        try:
            os.chdir(work / "a")
            export_census(source_zip=self.source, out="shared")
            os.chdir(work)
            export_census(source_zip=self.source, tables=["G02"], out="a/shared")
        finally:
            os.chdir(original_cwd)
        out = work / "a/shared"
        manifest = json.loads((out / "2021_GCP_POA_NSW_manifest.json").read_text())
        for entry in manifest["tables"].values():
            self.assertEqual(entry["file"], Path(entry["file"]).name)
            self.assertTrue((out / entry["file"]).is_file())
        moved = self.base / "moved"
        out.rename(moved)
        for entry in manifest["tables"].values():
            self.assertTrue((moved / entry["file"]).is_file())

    def test_old_manifest_absolute_and_cwd_relative_paths_are_migrated(self):
        for style in ["relative", "absolute", "windows"]:
            with self.subTest(style=style):
                out = self.base / f"legacy_paths_{style}"
                files = export_census(source_zip=self.source, out=out)
                path = out / "2021_GCP_POA_NSW_manifest.json"
                previous = json.loads(path.read_text())
                legacy_file = {"absolute": str(files["G01"].resolve()),
                               "relative": f"old/cwd/exports/{files['G01'].name}",
                               "windows": f"C:\\old\\exports\\{files['G01'].name}"}[style]
                previous["tables"]["G01"]["file"] = legacy_file
                path.write_text(json.dumps(previous))
                export_census(source_zip=self.source, tables=["G02"], out=out)
                current = json.loads(path.read_text())
                entry = current["tables"]["G01"]
                self.assertEqual(entry["file"], files["G01"].name)
                self.assertTrue((out / entry["file"]).is_file())
                self.assertEqual(entry["source"], previous["tables"]["G01"]["source"])

    def test_invalid_codes_fail_before_source_access_in_all_high_level_apis(self):
        invalid = [[], "2000", [800.5], [float("nan")], [float("inf")],
                   [True], [np.bool_(True)], [-800], [Decimal("800.0")],
                   [""], ["poa"], ["800.0"], 800]
        with patch("abs_census_etl._resolve_source", side_effect=AssertionError("source accessed")):
            for codes in invalid:
                for function in [load_census, load_census_tables, export_census]:
                    with self.subTest(codes=codes, function=function.__name__):
                        with self.assertRaises(ValueError):
                            function(codes=codes)
            with self.assertRaisesRegex(ValueError, "Invalid"):
                main(["--geo", "POA", "--codes", "800.5", "--out", str(self.base / "bad_codes")])
        self.assertFalse((self.base / "bad_codes").exists())

    def test_numeric_poa_types_keep_whole_number_support(self):
        source = make_nt_zip(self.base / "numeric_types.zip")
        for value in [np.float32(800), np.float64(800), Fraction(800, 1), np.int64(800)]:
            with self.subTest(value=value):
                df = load_census(source_zip=source, state="NT", codes=[value])
                self.assertEqual(df["POA_CODE_2021"].tolist(), ["POA0800"])

    @unittest.skipUnless(os.name == "posix", "POSIX mode/umask semantics")
    def test_first_manifest_follows_data_file_permissions_under_isolated_umasks(self):
        script = """
import json, os, stat, sys
from pathlib import Path
from unittest.mock import patch
from abs_census_etl import export_census
os.umask(int(sys.argv[1], 8))
with patch("abs_census_etl.os.umask", side_effect=AssertionError("umask changed during export")):
    paths = export_census(source_zip=sys.argv[2], out=sys.argv[3])
manifest = Path(sys.argv[3]) / "2021_GCP_POA_NSW_manifest.json"
print(json.dumps([stat.S_IMODE(paths["G01"].stat().st_mode),
                  stat.S_IMODE(manifest.stat().st_mode)]))
"""
        for mask in [0o022, 0o027, 0o077]:
            with self.subTest(mask=oct(mask)):
                out = self.base / f"permissions_{mask:o}"
                run = subprocess.run([sys.executable, "-c", script, f"{mask:o}",
                                      str(self.source), str(out)], cwd=self.base,
                                     env=dict(os.environ, PYTHONPATH=str(ROOT)),
                                     capture_output=True, text=True)
                self.assertEqual(run.returncode, 0, run.stderr)
                self.assertEqual(json.loads(run.stdout), [0o666 & ~mask] * 2)

    @unittest.skipUnless(os.name == "posix", "POSIX permission bits")
    def test_existing_manifest_permissions_take_priority_over_data_permissions(self):
        out = self.base / "existing_permissions"
        paths = export_census(source_zip=self.source, out=out)
        manifest = out / "2021_GCP_POA_NSW_manifest.json"
        for mode in [0o600, 0o640, 0o644, 0o660]:
            with self.subTest(mode=oct(mode)):
                manifest.chmod(mode)
                paths["G01"].chmod(0o664)
                export_census(source_zip=self.source, out=out)
                self.assertEqual(stat.S_IMODE(manifest.stat().st_mode), mode)
                self.assertEqual(stat.S_IMODE(paths["G01"].stat().st_mode), 0o664)
                self.assertEqual(list(out.rglob(".*.tmp")), [])

    def test_manifest_permission_copy_failure_preserves_old_file_and_cleans_temp(self):
        out = self.base / "permission_failure"
        export_census(source_zip=self.source, out=out)
        manifest = out / "2021_GCP_POA_NSW_manifest.json"
        before = manifest.read_bytes()
        before_mode = stat.S_IMODE(manifest.stat().st_mode)
        with patch("abs_census_etl.shutil.copymode", side_effect=PermissionError("copy failed")):
            with self.assertRaisesRegex(PermissionError, "copy failed"):
                export_census(source_zip=self.source, tables=["G02"], out=out)
        self.assertEqual(manifest.read_bytes(), before)
        self.assertEqual(stat.S_IMODE(manifest.stat().st_mode), before_mode)
        self.assertEqual(list(out.rglob(".*.tmp")), [])
        export_census(source_zip=self.source, tables=["G02"], out=out)
        self.assertIn("G02", json.loads(manifest.read_text())["tables"])

    def test_read_only_temp_cleanup_does_not_mask_replace_error(self):
        # Emulate Windows on every platform: the temporary file inherits a
        # read-only mode, the replacement is refused (another process holds the
        # manifest open), and read-only files cannot be deleted until the flag
        # clears. Read-only existing outputs are refused earlier by preflight.
        out = self.base / "read_only_temp"
        export_census(source_zip=self.source, out=out)
        manifest = out / "2021_GCP_POA_NSW_manifest.json"
        before = manifest.read_bytes()
        real_unlink = Path.unlink

        def read_only_copymode(source, destination):
            os.chmod(destination, stat.S_IREAD)

        def windows_unlink(path, missing_ok=False):
            if path.exists() and not path.stat().st_mode & stat.S_IWRITE:
                raise PermissionError(f"read-only file: {path}")
            return real_unlink(path, missing_ok=missing_ok)

        with patch("abs_census_etl.shutil.copymode", side_effect=read_only_copymode):
            with patch.object(Path, "replace", side_effect=PermissionError("manifest is locked")):
                with patch.object(Path, "unlink", windows_unlink):
                    with self.assertRaisesRegex(PermissionError, "manifest is locked"):
                        export_census(source_zip=self.source, tables=["G02"], out=out)
        self.assertEqual(manifest.read_bytes(), before)
        self.assertEqual(list(out.rglob(".*.tmp")), [])

    def test_failed_temp_cleanup_never_masks_original_manifest_error(self):
        for unlink_error in [PermissionError("cannot delete"), OSError("file busy")]:
            with self.subTest(unlink_error=type(unlink_error).__name__):
                out = self.base / f"cleanup_failure_{type(unlink_error).__name__}"
                export_census(source_zip=self.source, out=out)
                manifest = out / "2021_GCP_POA_NSW_manifest.json"
                before = manifest.read_bytes()
                with patch.object(Path, "replace", side_effect=OSError("rename failed")):
                    with patch.object(Path, "unlink", side_effect=unlink_error):
                        with patch.object(Path, "chmod", side_effect=OSError("cannot chmod")):
                            with self.assertRaisesRegex(OSError, "rename failed") as caught:
                                export_census(source_zip=self.source, tables=["G02"], out=out)
                self.assertIn("Could not remove staging folder", caught.exception.__notes__[0])
                self.assertEqual(manifest.read_bytes(), before)
                # The undeletable temporary file stays behind and is never read.
                self.assertEqual(len(list(out.rglob(".*.tmp"))), 1)
                with self.assertWarnsRegex(RuntimeWarning, "Staging folders without a recovery journal"):
                    export_census(source_zip=self.source, tables=["G02"], out=out)
                self.assertIn("G02", json.loads(manifest.read_text())["tables"])

    @unittest.skipIf(RUNNING_AS_ROOT, "root bypasses permission bits")
    def test_read_only_manifest_is_refused_before_source_access(self):
        out = self.base / "read_only_refusal"
        export_census(source_zip=self.source, out=out)
        manifest = out / "2021_GCP_POA_NSW_manifest.json"
        manifest.chmod(stat.S_IREAD)
        before = {p.name: p.read_bytes() for p in out.iterdir()}
        with patch("abs_census_etl._resolve_source", side_effect=AssertionError("source accessed")):
            with self.assertRaisesRegex(PermissionError, "Read-only export output: .*manifest"):
                export_census(source_zip=self.source, tables=["G02"], out=out)
        cli = subprocess.run([sys.executable, str(ROOT / "abs_census_etl.py"),
                              "--geo", "POA", "--zip", str(self.source),
                              "--tables", "G02", "--out", str(out)],
                             capture_output=True, text=True)
        self.assertEqual(cli.returncode, 1)
        self.assertIn("Make it writable or use a fresh output folder", cli.stderr)
        self.assertEqual({p.name: p.read_bytes() for p in out.iterdir()}, before)

    @unittest.skipIf(RUNNING_AS_ROOT, "root bypasses permission bits")
    def test_read_only_data_file_is_refused_before_any_table_is_written(self):
        out = self.base / "read_only_data"
        paths = export_census(source_zip=self.source, tables=["G01", "G02"], out=out)
        paths["G02"].chmod(stat.S_IREAD)
        before = {p.name: p.read_bytes() for p in out.iterdir()}
        with patch("abs_census_etl._resolve_source", side_effect=AssertionError("source accessed")):
            with self.assertRaisesRegex(PermissionError, "G02_wide.csv"):
                export_census(source_zip=self.source, tables=["G01", "G02"],
                              codes=["2000"], out=out)
        # G01 is written first in a normal run; it must not have been touched.
        self.assertEqual({p.name: p.read_bytes() for p in out.iterdir()}, before)
        # A read-only file of the other shape is not a target and does not block.
        files = export_census(source_zip=self.source, tables=["G02"], long=True, out=out)
        self.assertEqual(files["G02"].name, "2021_GCP_POA_NSW_G02_long.csv")

    @unittest.skipUnless(os.name == "posix", "POSIX directory permissions")
    @unittest.skipIf(RUNNING_AS_ROOT, "root bypasses permission bits")
    def test_read_only_output_folder_is_refused_before_any_write(self):
        out = self.base / "read_only_folder"
        export_census(source_zip=self.source, out=out)
        before = {p.name: p.read_bytes() for p in out.iterdir()}
        out.chmod(0o555)
        self.addCleanup(out.chmod, 0o755)
        with patch("abs_census_etl._resolve_source", side_effect=AssertionError("source accessed")):
            with self.assertRaisesRegex(PermissionError, "Read-only export output"):
                export_census(source_zip=self.source, codes=["2000"], out=out)
        self.assertEqual({p.name: p.read_bytes() for p in out.iterdir()}, before)

    @unittest.skipUnless(os.name == "posix", "POSIX permission bits")
    def test_first_parquet_manifest_matches_its_data_file_permissions(self):
        try:
            import pyarrow  # noqa: F401
        except ImportError:
            self.skipTest("pyarrow is not installed")
        out = self.base / "parquet_permissions"
        paths = export_census(source_zip=self.source, out=out, file_format="parquet")
        manifest = out / "2021_GCP_POA_NSW_manifest.json"
        self.assertEqual(stat.S_IMODE(manifest.stat().st_mode),
                         stat.S_IMODE(paths["G01"].stat().st_mode))

    def test_second_table_write_failure_never_changes_published_export(self):
        out = self.base / "staged_write_failure"
        export_census(source_zip=self.source, tables=["G01", "G02"], out=out)
        before = {p.name: p.read_bytes() for p in out.iterdir()}
        other = make_zip(self.base / "b.zip", poa_g01=POA_G01.replace("400", "999"))
        real_write = pd.DataFrame.to_csv
        for failure in [OSError("disk full"), PermissionError("Excel lock"), KeyboardInterrupt()]:
            with self.subTest(failure=type(failure).__name__):
                def write(frame, path, *args, **kwargs):
                    if "_G02_" in Path(path).name:
                        Path(path).write_text("partial data")
                        raise failure
                    return real_write(frame, path, *args, **kwargs)
                with patch.object(pd.DataFrame, "to_csv", write):
                    with self.assertRaises(type(failure)):
                        export_census(source_zip=other, tables=["G01", "G02"],
                                      codes=["2000"], out=out)
                self.assertEqual({p.name: p.read_bytes() for p in out.iterdir()}, before)

    def test_second_publish_failure_rolls_back_first_table_and_provenance(self):
        out = self.base / "publish_failure"
        paths = export_census(source_zip=self.source, tables=["G01", "G02"], out=out)
        before = {p.name: p.read_bytes() for p in out.iterdir()}
        other = make_zip(self.base / "b.zip", poa_g01=POA_G01.replace("400", "999"))
        real_replace = Path.replace
        for failure in [PermissionError("Excel lock"), KeyboardInterrupt()]:
            observed = []
            with self.subTest(failure=type(failure).__name__):
                def replace(pending, destination):
                    if Path(destination) == paths["G02"] and pending.name == paths["G02"].name + ".part":
                        observed.append(pd.read_csv(paths["G01"])["Tot_P_P"].tolist())
                        raise failure
                    return real_replace(pending, destination)
                with patch.object(Path, "replace", replace):
                    with self.assertRaises(type(failure)):
                        export_census(source_zip=other, tables=["G01", "G02"],
                                      codes=["2000"], out=out)
                self.assertEqual(observed, [[999]])
                self.assertEqual({p.name: p.read_bytes() for p in out.iterdir()}, before,
                                 "published bytes changed after rollback")

    def test_manifest_publish_failure_rolls_back_all_data(self):
        out = self.base / "manifest_publish_failure"
        export_census(source_zip=self.source, tables=["G01", "G02"], out=out)
        before = {p.name: p.read_bytes() for p in out.iterdir()}
        target = out / "2021_GCP_POA_NSW_manifest.json"
        real_replace = Path.replace
        def replace(pending, destination):
            if Path(destination) == target:
                raise PermissionError("manifest locked")
            return real_replace(pending, destination)
        with patch.object(Path, "replace", replace):
            with self.assertRaisesRegex(PermissionError, "manifest locked"):
                export_census(source_zip=self.source, tables=["G01", "G02"],
                              codes=["2000"], out=out)
        self.assertEqual({p.name: p.read_bytes() for p in out.iterdir()}, before)

    def test_first_export_publish_failure_removes_newly_published_files(self):
        out = self.base / "first_publish_failure"
        real_replace = Path.replace
        def replace(pending, destination):
            if Path(destination).parent == out and "_G02_" in Path(destination).name:
                raise PermissionError("file locked")
            return real_replace(pending, destination)
        with patch.object(Path, "replace", replace):
            with self.assertRaises(PermissionError):
                export_census(source_zip=self.source, tables=["G01", "G02"], out=out)
        self.assertEqual(list(out.iterdir()), [out / ".abs-census-etl.lock"])

    def test_rollback_failure_retains_backup_and_reports_recovery(self):
        out = self.base / "rollback_failure"
        paths = export_census(source_zip=self.source, tables=["G01", "G02"], out=out)
        original_g01 = paths["G01"].read_bytes()
        real_replace = Path.replace
        def replace(pending, destination):
            if Path(destination) == paths["G02"] and pending.name == paths["G02"].name + ".part":
                raise PermissionError("Excel lock")
            if Path(destination) == paths["G01"] and pending.name.startswith("backup-"):
                raise PermissionError("rollback blocked")
            return real_replace(pending, destination)
        with patch.object(Path, "replace", replace):
            with self.assertRaisesRegex(OSError, "rollback was incomplete.*Recovery files") as caught:
                export_census(source_zip=self.source, tables=["G01", "G02"],
                              codes=["2000"], out=out)
        recovery = list(out.glob(".abs-export-*"))
        self.assertEqual(len(recovery), 1)
        self.assertIn(str(recovery[0]), str(caught.exception))
        self.assertIn("Do not rely on the manifest", str(caught.exception))
        mapping = json.loads((recovery[0] / "recovery.json").read_text())
        backup = recovery[0] / mapping["backups"][paths["G01"].name]
        self.assertEqual(backup.read_bytes(), original_g01)
        self.assertIsInstance(caught.exception.__cause__, PermissionError)

    def test_missing_parquet_engines_fail_before_source_access(self):
        with patch("abs_census_etl.find_spec", return_value=None):
            with patch("abs_census_etl._resolve_source", side_effect=AssertionError("source accessed")):
                with self.assertRaisesRegex(ValueError, "Parquet export needs") as caught:
                    export_census(file_format="parquet", out=self.base / "missing_engine")
        message = str(caught.exception)
        for advice in ("parquet extra", "Install or update for managed installs",
                       "Setup for pip", "Platforms and development for uv",
                       "--format csv", "file_format='csv'"):
            self.assertIn(advice, message)
        self.assertNotIn("pip install", message)
        self.assertNotIn("remove build", message)
        self.assertFalse((self.base / "missing_engine").exists())
        for available in ["pyarrow", "fastparquet"]:
            with self.subTest(available=available):
                with patch("abs_census_etl.find_spec", side_effect=lambda name: object() if name == available else None):
                    with patch("abs_census_etl._resolve_source", side_effect=FileNotFoundError("source sentinel")):
                        with self.assertRaisesRegex(FileNotFoundError, "source sentinel"):
                            export_census(file_format="parquet")

    def test_output_path_and_ancestor_files_are_rejected_before_source_access(self):
        file = self.base / "regular_file"
        file.write_text("keep")
        with patch("abs_census_etl._resolve_source", side_effect=AssertionError("source accessed")):
            for out in [file, file / "child" / "exports"]:
                with self.subTest(out=out):
                    with self.assertRaisesRegex(NotADirectoryError, "must be a directory"):
                        export_census(out=out)
        self.assertEqual(file.read_text(), "keep")

    def test_missing_output_parent_permissions_are_checked_before_source_access(self):
        parent = self.base / "existing_parent"
        parent.mkdir()
        out = parent / "new" / "exports"
        real_access = os.access
        checks = []
        def access(path, flags):
            if Path(path) == parent:
                checks.append(flags)
                return False
            return real_access(path, flags)
        with patch("abs_census_etl.os.access", side_effect=access):
            with patch("abs_census_etl._resolve_source", side_effect=AssertionError("source accessed")):
                with self.assertRaisesRegex(PermissionError, "Read-only export output"):
                    export_census(out=out)
        self.assertEqual(checks, [os.W_OK | os.X_OK])
        self.assertFalse((parent / "new").exists())

    def test_symlinked_export_files_are_rejected_before_source_access(self):
        for manifest_link in [False, True]:
            with self.subTest(manifest_link=manifest_link):
                out = self.base / f"symlinks_{manifest_link}"
                out.mkdir()
                referent = self.base / f"referent_{manifest_link}"
                referent.write_text("keep referent")
                name = ("2021_GCP_POA_NSW_manifest.json" if manifest_link
                        else "2021_GCP_POA_NSW_G01_wide.csv")
                link = out / name
                try:
                    link.symlink_to(referent)
                except OSError as exc:
                    self.skipTest(f"symlinks unavailable: {exc}")
                with patch("abs_census_etl._resolve_source", side_effect=AssertionError("source accessed")):
                    with self.assertRaisesRegex(ValueError, "Symlinked export files"):
                        export_census(out=out)
                self.assertTrue(link.is_symlink())
                self.assertEqual(referent.read_text(), "keep referent")

    def test_output_directory_symlink_is_supported(self):
        actual = self.base / "actual_exports"
        actual.mkdir()
        link = self.base / "linked_exports"
        try:
            link.symlink_to(actual, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"symlinks unavailable: {exc}")
        files = export_census(source_zip=self.source, out=link)
        self.assertTrue(files["G01"].is_file())
        self.assertTrue((actual / "2021_GCP_POA_NSW_manifest.json").exists())

    def test_invalid_keep_fails_before_source_access_in_every_api(self):
        with patch("abs_census_etl._resolve_source", side_effect=AssertionError("source accessed")):
            for keep in ["Tot_P_P", b"Tot_P_P", [], [" "], [None], 42]:
                for function in [load_census, load_census_tables, export_census]:
                    with self.subTest(keep=keep, function=function.__name__):
                        with self.assertRaisesRegex(ValueError, "keep must"):
                            function(keep=keep)
            with self.assertRaisesRegex(ValueError, "keep must"):
                main(["--geo", "POA", "--keep", " "])

    def test_source_provenance_is_absolute_across_working_directories(self):
        work = self.base / "sources"
        (work / "a").mkdir(parents=True)
        (work / "b").mkdir()
        source_a = make_zip(work / "a/a.zip")
        source_b = make_zip(work / "b/b.zip")
        original_cwd = Path.cwd()
        try:
            os.chdir(work / "a")
            export_census(source_zip="a.zip", out="../shared")
            os.chdir(work / "b")
            export_census(source_zip="b.zip", tables=["G02"], out="../shared")
        finally:
            os.chdir(original_cwd)
        path = work / "shared/2021_GCP_POA_NSW_manifest.json"
        manifest = json.loads(path.read_text())
        self.assertEqual(manifest["tables"]["G01"]["source"], str(source_a.resolve()))
        self.assertEqual(manifest["tables"]["G02"]["source"], str(source_b.resolve()))
        self.assertEqual(manifest["source"], str(source_b.resolve()))
        for entry in manifest["tables"].values():
            self.assertEqual(entry["source_path_kind"], "absolute")
        manifest["tables"]["G01"]["source"] = "old-relative.zip"
        manifest["tables"]["G01"].pop("source_path_kind", None)
        path.write_text(json.dumps(manifest))
        export_census(source_zip=source_b, tables=["G02"], out=path.parent)
        retained = json.loads(path.read_text())["tables"]["G01"]
        self.assertEqual(retained["source"], "old-relative.zip")
        self.assertEqual(retained["source_path_kind"], "legacy_cwd_relative")

    def test_backup_failure_happens_before_any_published_file_changes(self):
        out = self.base / "backup_failure"
        export_census(source_zip=self.source, tables=["G01", "G02"], out=out)
        before = {p.name: p.read_bytes() for p in out.iterdir()}
        real_copy = shutil.copy2
        def copy(source, destination, *args, **kwargs):
            if "_G02_" in Path(source).name:
                raise OSError("no backup space")
            return real_copy(source, destination, *args, **kwargs)
        with patch("abs_census_etl.shutil.copy2", side_effect=copy):
            with self.assertRaisesRegex(OSError, "no backup space"):
                export_census(source_zip=self.source, tables=["G01", "G02"],
                              codes=["2000"], out=out)
        self.assertEqual({p.name: p.read_bytes() for p in out.iterdir()}, before)

    def test_missing_root_parent_walk_terminates_before_source_access(self):
        checks = []
        def missing(path):
            checks.append(path)
            if len(checks) > 40:
                raise AssertionError("parent walk exceeded bound")
            return False
        with patch.object(Path, "exists", missing):
            with patch.object(Path, "is_symlink", return_value=False):
                with patch("abs_census_etl._resolve_source", side_effect=AssertionError("source accessed")):
                    with self.assertRaisesRegex(FileNotFoundError, "drive or network share"):
                        export_census(out=self.base / "missing_drive/exports")
        self.assertLessEqual(len(checks), 40)

    def test_partial_rollback_journal_tracks_each_file_and_blocks_new_exports(self):
        out = self.base / "three_table_recovery"
        paths = export_census(source_zip=self.source, tables=["G01", "G02", "G04"], out=out)
        before = {p.name: p.read_bytes() for p in out.iterdir()}
        real_replace = Path.replace
        def replace(pending, destination):
            if Path(destination) == paths["G04"] and pending.name.endswith(".part"):
                raise PermissionError("G04 publish blocked")
            if Path(destination) == paths["G02"] and pending.name.startswith("backup-"):
                raise PermissionError("G02 rollback blocked")
            return real_replace(pending, destination)
        with patch.object(Path, "replace", replace):
            with self.assertRaisesRegex(OSError, "rollback was incomplete"):
                export_census(source_zip=self.source, tables=["G01", "G02", "G04"],
                              codes=["2000"], out=out)
        work = next(out.glob(".abs-export-*"))
        journal = json.loads((work / "recovery.json").read_text())
        self.assertEqual(journal["state"], "rollback_incomplete",
                         "rollback journal state was not updated")
        self.assertEqual(journal["files"][paths["G01"].name]["status"], "restored")
        self.assertIsNone(journal["files"][paths["G01"].name]["backup"])
        self.assertNotIn(paths["G01"].name, journal["backups"])
        self.assertEqual(journal["files"][paths["G02"].name]["status"], "rollback_failed")
        self.assertEqual(journal["files"][paths["G04"].name]["status"], "never_published")
        self.assertEqual(journal["files"]["2021_GCP_POA_NSW_manifest.json"]["status"], "never_published")
        for backup in journal["backups"].values():
            self.assertTrue((work / backup).exists())
        self.assertEqual(paths["G01"].read_bytes(), before[paths["G01"].name])
        self.assertEqual(paths["G04"].read_bytes(), before[paths["G04"].name])
        self.assertEqual(len(pd.read_csv(paths["G02"])), 1)
        self.assertEqual(set(out.rglob("*.csv")), set(paths.values()))
        with patch("abs_census_etl._resolve_source", side_effect=AssertionError("source accessed")):
            with self.assertRaisesRegex(OSError, "Unresolved export recovery journal"):
                export_census(out=out)
        # Simulate the human-selected repair, then archive the reviewed journal.
        (work / journal["backups"][paths["G02"].name]).replace(paths["G02"])
        work.rename(self.base / "archived_recovery")
        export_census(source_zip=self.source, tables=["G01", "G02", "G04"], out=out)
        self.assertFalse(list(out.glob(".abs-export-*/recovery.json")))

    def test_retained_recovery_journals_block_before_source_even_when_malformed(self):
        out = self.base / "leftover_recovery"
        work = out / ".abs-export-leftover"
        work.mkdir(parents=True)
        journal = work / "recovery.json"
        for content, state in [('partial json', "unreadable"), ('["publishing"]', "unreadable"),
                               ('{"state": "publishing"}', "publishing"),
                               ('{"state": "committed"}', "committed")]:
            with self.subTest(content=content):
                journal.write_text(content)
                with patch("abs_census_etl._resolve_source", side_effect=AssertionError("source accessed")):
                    with self.assertRaisesRegex(OSError, "Unresolved export recovery journal") as caught:
                        export_census(out=out)
                self.assertIn(f"recovery.json (state: {state})", str(caught.exception))
                self.assertEqual(journal.read_text(), content)

    def test_failed_rollback_journal_update_is_retained_and_blocks_new_export(self):
        out = self.base / "journal_failure"
        paths = export_census(source_zip=self.source, tables=["G01", "G02"], out=out)
        before = {p.name: p.read_bytes() for p in out.iterdir()}
        real_replace = Path.replace
        import abs_census_etl as module
        real_manifest = module._write_manifest
        def replace(pending, destination):
            if Path(destination) == paths["G02"] and pending.name.endswith(".part"):
                raise PermissionError("publish blocked")
            return real_replace(pending, destination)
        def write(path, manifest, **kwargs):
            if path.name == "recovery.json" and manifest.get("state") == "rolled_back":
                raise OSError("journal disk full")
            return real_manifest(path, manifest, **kwargs)
        with patch.object(Path, "replace", replace):
            with patch("abs_census_etl._write_manifest", side_effect=write):
                with self.assertRaisesRegex(OSError, "Journal may be stale"):
                    export_census(source_zip=self.source, tables=["G01", "G02"],
                                  codes=["2000"], out=out)
        for name, content in before.items():
            self.assertEqual((out / name).read_bytes(), content)
        self.assertTrue(list(out.glob(".abs-export-*/recovery.json")))
        with patch("abs_census_etl._resolve_source", side_effect=AssertionError("source accessed")):
            with self.assertRaisesRegex(OSError, "Unresolved export recovery journal"):
                export_census(out=out)

    def test_hard_exit_during_publication_leaves_blocking_journal(self):
        out = self.base / "crashed_publish"
        export_census(source_zip=self.source, tables=["G01", "G02"], out=out)
        script = """
import os, sys
from pathlib import Path
from unittest.mock import patch
from abs_census_etl import export_census
real = Path.replace
out = Path(sys.argv[2])
def replace(pending, destination):
    result = real(pending, destination)
    if Path(destination).parent == out and "_G01_" in Path(destination).name:
        os._exit(23)
    return result
with patch.object(Path, "replace", replace):
    export_census(source_zip=sys.argv[1], tables=["G01", "G02"], codes=["2000"], out=out)
"""
        run = subprocess.run([sys.executable, "-c", script, str(self.source), str(out)],
                             env=dict(os.environ, PYTHONPATH=str(ROOT)), capture_output=True, text=True)
        self.assertEqual(run.returncode, 23, run.stderr)
        journal = next(out.glob(".abs-export-*/recovery.json"))
        self.assertEqual(json.loads(journal.read_text())["state"], "publishing")
        with patch("abs_census_etl._resolve_source", side_effect=AssertionError("source accessed")):
            with self.assertRaisesRegex(OSError, "Unresolved export recovery journal"):
                export_census(out=out)
        self.assertEqual(len(list(out.rglob("*.csv"))), 2)

    def test_hard_exit_during_staging_exposes_no_unpublished_csv_names(self):
        out = self.base / "crashed_staging"
        paths = export_census(source_zip=self.source, tables=["G01", "G02"], out=out)
        before = {path.name: path.read_bytes() for path in paths.values()}
        script = """
import os, sys
from pathlib import Path
from unittest.mock import patch
import pandas as pd
from abs_census_etl import export_census
real = pd.DataFrame.to_csv
def write(frame, path, *args, **kwargs):
    if "_G02_" in Path(path).name:
        os._exit(19)
    return real(frame, path, *args, **kwargs)
with patch.object(pd.DataFrame, "to_csv", write):
    export_census(source_zip=sys.argv[1], tables=["G01", "G02"], codes=["2000"], out=sys.argv[2])
"""
        run = subprocess.run([sys.executable, "-c", script, str(self.source), str(out)],
                             env=dict(os.environ, PYTHONPATH=str(ROOT)), capture_output=True, text=True)
        self.assertEqual(run.returncode, 19, run.stderr)
        self.assertEqual(set(out.rglob("*.csv")), set(paths.values()))
        self.assertTrue(list(out.glob(".abs-export-*/*.part")))
        self.assertFalse(list(out.glob(".abs-export-*/recovery.json")))
        for name, content in before.items():
            self.assertEqual((out / name).read_bytes(), content)

    @unittest.skipUnless(os.name == "posix", "POSIX permission modes")
    def test_data_file_mode_survives_replacement_under_distinct_umasks(self):
        script = """
import json, os, stat, sys
from pathlib import Path
from abs_census_etl import export_census
source, root = sys.argv[1], Path(sys.argv[2])
modes = []
for mask in [0o002, 0o022]:
    os.umask(mask)
    out = root / str(mask)
    files = export_census(source_zip=source, out=out)
    files["G01"].chmod(0o604)
    export_census(source_zip=source, codes=["2000"], out=out)
    modes.append(stat.S_IMODE(files["G01"].stat().st_mode))
print(json.dumps(modes))
"""
        run = subprocess.run([sys.executable, "-c", script, str(self.source), str(self.base / "modes")],
                             env=dict(os.environ, PYTHONPATH=str(ROOT)), capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(json.loads(run.stdout), [0o604, 0o604], "data permission mode was not preserved")

    def test_source_path_kind_uses_both_path_families_and_preserves_absolute_label(self):
        for source, existing, expected in [
                ("/home/example/x.zip", None, "absolute"),
                ("C:\\example\\x.zip", None, "absolute"),
                ("relative.zip", None, "legacy_cwd_relative"),
                ("relative.zip", "absolute", "absolute")]:
            with self.subTest(source=source, existing=existing):
                out = self.base / f"source_kind_{len(list(self.base.iterdir()))}"
                export_census(source_zip=self.source, out=out)
                path = out / "2021_GCP_POA_NSW_manifest.json"
                manifest = json.loads(path.read_text())
                entry = manifest["tables"]["G01"]
                entry["source"] = source
                entry.pop("source_path_kind", None)
                if existing:
                    entry["source_path_kind"] = existing
                path.write_text(json.dumps(manifest))
                export_census(source_zip=self.source, tables=["G02"], out=out)
                retained = json.loads(path.read_text())["tables"]["G01"]
                self.assertEqual(retained["source"], source)
                self.assertEqual(retained["source_path_kind"], expected)

    def test_uninspectable_recovery_folder_is_not_silently_ignored(self):
        out = self.base / "uninspectable"
        work = out / ".abs-export-private"
        work.mkdir(parents=True)
        journal = work / "recovery.json"
        journal.write_text("private journal")
        real_stat = Path.stat
        def stat(path, *args, **kwargs):
            if path == journal:
                raise PermissionError("cannot inspect recovery")
            return real_stat(path, *args, **kwargs)
        with patch.object(Path, "stat", stat):
            with patch("abs_census_etl._resolve_source", side_effect=AssertionError("source accessed")):
                with self.assertRaisesRegex(OSError, "Unresolved export recovery journal"):
                    export_census(out=out)

    def test_successful_export_warns_when_its_recovery_journal_cannot_be_removed(self):
        out = self.base / "undeletable_journal"
        export_census(source_zip=self.source, out=out)
        real_unlink = Path.unlink
        def busy(path, missing_ok=False):
            if path.name == "recovery.json":
                raise PermissionError("journal held by another process")
            return real_unlink(path, missing_ok=missing_ok)
        with patch.object(Path, "unlink", busy):
            with self.assertWarnsRegex(RuntimeWarning, "later exports to .* are refused"):
                files = export_census(source_zip=self.source, codes=["2000"], out=out)
        self.assertEqual(len(pd.read_csv(files["G01"])), 1)
        (work,) = out.glob(".abs-export-*")
        self.assertEqual([path.name for path in work.iterdir()], ["recovery.json"])
        with patch("abs_census_etl._resolve_source", side_effect=AssertionError("source accessed")):
            with self.assertRaisesRegex(OSError, r"recovery\.json \(state: committed\)"):
                export_census(out=out)

    def test_interrupted_cleanup_removes_journal_before_other_staging_files(self):
        out = self.base / "interrupted_cleanup"
        export_census(source_zip=self.source, out=out)
        import abs_census_etl as module
        real_remove, real_iterdir = module._remove_temporary, Path.iterdir
        def remove(path):
            if path.name.startswith("backup-"):
                raise KeyboardInterrupt
            return real_remove(path)
        def journal_last(path):
            return iter(sorted(real_iterdir(path), key=lambda child: child.name == "recovery.json"))
        with patch("abs_census_etl._remove_temporary", side_effect=remove):
            with patch.object(Path, "iterdir", journal_last):
                with self.assertRaises(KeyboardInterrupt):
                    export_census(source_zip=self.source, codes=["2000"], out=out)
        (work,) = out.glob(".abs-export-*")
        self.assertIsNone(module._recovery_journal(work), "journal outlived interrupted cleanup")
        self.assertTrue(list(work.glob("backup-*")))

    def test_journal_less_staging_folders_are_reported_and_kept(self):
        out = self.base / "orphan_staging"
        export_census(source_zip=self.source, out=out)
        orphan = out / ".abs-export-orphan"
        orphan.mkdir()
        payload = orphan / "2021_GCP_POA_NSW_G01_wide.csv.part"
        payload.write_text("unpublished")
        with self.assertWarnsRegex(RuntimeWarning, "Staging folders without a recovery journal") as caught:
            files = export_census(source_zip=self.source, codes=["2000"], out=out)
        self.assertIn(str(orphan), str(caught.warning))
        self.assertEqual(len(pd.read_csv(files["G01"])), 1)
        self.assertEqual(payload.read_text(), "unpublished")

    def test_failed_export_notes_leftover_journal_without_replacing_error(self):
        out = self.base / "failed_leftover"
        paths = export_census(source_zip=self.source, tables=["G01", "G02"], out=out)
        before = {p.name: p.read_bytes() for p in out.iterdir()}
        real_replace, real_unlink = Path.replace, Path.unlink
        def replace(pending, destination):
            if Path(destination) == paths["G02"] and pending.name.endswith(".part"):
                raise PermissionError("G02 locked")
            return real_replace(pending, destination)
        def busy(path, missing_ok=False):
            if path.name == "recovery.json":
                raise PermissionError("journal held by another process")
            return real_unlink(path, missing_ok=missing_ok)
        with patch.object(Path, "replace", replace), patch.object(Path, "unlink", busy):
            with self.assertRaisesRegex(PermissionError, "G02 locked") as caught:
                export_census(source_zip=self.source, tables=["G01", "G02"],
                              codes=["2000"], out=out)
        self.assertIn("later exports", caught.exception.__notes__[0])
        self.assertEqual({p.name: p.read_bytes() for p in out.iterdir() if p.is_file()}, before)
        with patch("abs_census_etl._resolve_source", side_effect=AssertionError("source accessed")):
            with self.assertRaisesRegex(OSError, r"state: rolled_back"):
                export_census(out=out)
        # The CLI prints the note after the original error.
        script = """
import runpy, sys
from pathlib import Path
from unittest.mock import patch
real_replace, real_unlink = Path.replace, Path.unlink
source, out, module = sys.argv[1], Path(sys.argv[2]), sys.argv[3]
def replace(pending, destination):
    if Path(destination) == out / "2021_GCP_POA_NSW_G02_wide.csv" and pending.name.endswith(".part"):
        raise PermissionError("G02 locked")
    return real_replace(pending, destination)
def busy(path, missing_ok=False):
    if path.name == "recovery.json":
        raise PermissionError("journal held by another process")
    return real_unlink(path, missing_ok=missing_ok)
sys.argv = [module, "--geo", "POA", "--zip", source, "--tables", "G01", "G02",
            "--codes", "2000", "--out", str(out)]
with patch.object(Path, "replace", replace), patch.object(Path, "unlink", busy):
    runpy.run_path(module, run_name="__main__")
"""
        cli_out = self.base / "failed_leftover_cli"
        export_census(source_zip=self.source, tables=["G01", "G02"], out=cli_out)
        run = subprocess.run([sys.executable, "-c", script, str(self.source), str(cli_out),
                              str(ROOT / "abs_census_etl.py")], capture_output=True, text=True)
        self.assertEqual(run.returncode, 1, run.stderr)
        self.assertIn("ERROR: G02 locked", run.stderr)
        self.assertIn("Could not remove recovery journal", run.stderr)

    def test_cli_prints_export_warnings_even_when_export_fails(self):
        out = self.base / "cli_warning"
        (out / ".abs-export-orphan").mkdir(parents=True)
        stderr = io.StringIO()
        with patch("sys.stdout", new_callable=io.StringIO), patch("sys.stderr", stderr):
            self.assertEqual(main(["--geo", "POA", "--zip", str(self.source), "--out", str(out)]), 0)
        self.assertIn("WARNING: Staging folders without a recovery journal", stderr.getvalue())
        stderr = io.StringIO()
        with patch("sys.stderr", stderr):
            with self.assertRaisesRegex(ValueError, "Unknown column"):
                main(["--geo", "POA", "--zip", str(self.source), "--tables", "G02",
                      "--keep", "missing_measure", "--out", str(out)])
        self.assertIn("WARNING: Staging folders without a recovery journal", stderr.getvalue())
        # Format refusal must retain the same orphan warning, before ZIP access.
        (out / "2021_GCP_POA_NSW_G02_wide.parquet").write_bytes(b"old export")
        stderr = io.StringIO()
        with patch("sys.stderr", stderr):
            with patch("abs_census_etl._validate_zip", side_effect=AssertionError("source accessed")):
                with self.assertRaises(FileExistsError):
                    main(["--geo", "POA", "--zip", str(self.source), "--tables", "G02",
                          "--out", str(out)])
        self.assertIn("WARNING: Staging folders without a recovery journal", stderr.getvalue())

    def test_datapack_url_2021(self):
        self.assertTrue(datapack_url("poa", "nsw").endswith(
            "2021_GCP_POA_for_NSW_short-header.zip"))


if __name__ == "__main__":
    unittest.main()
