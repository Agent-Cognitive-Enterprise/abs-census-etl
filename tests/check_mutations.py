"""Run focused regression mutations against temporary copies, never the checkout.

Usage: .venv/bin/python tests/check_mutations.py
A mutation counts as caught only when its intended test fails with the expected
assertion/runtime reason. Syntax/import failures and zero-test runs do not count.
"""
from __future__ import annotations

from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
_DESTINATION_ARGUMENTS = (
    '            out_dir=out_dir, existing_parent=existing_parent, geo=geo, state=state,\n'
    '            table_ids=table_ids, long=long, file_format=file_format,\n'
    '            warned_orphans=warned_orphans)'
)
MUTATIONS = [
    ("allow numeric CSV inference", 'dtype="string", keep_default_na=False',
     'dtype=None, keep_default_na=False',
     "test_whitespace_in_raw_code_column_still_preserves_leading_zeroes", "AttributeError"),
    ("remove input trimming", 'value.strip().upper().removeprefix("POA")',
     'value.upper().removeprefix("POA")',
     "test_nsw_filter_input_case_and_whitespace", "Invalid POA"),
    ("remove input uppercase", 'value.strip().upper().removeprefix("POA")',
     'value.strip().removeprefix("POA")',
     "test_nsw_filter_input_case_and_whitespace", "Invalid POA"),
    ("remove postcode padding", 'return value.zfill(4) if re.fullmatch(r"[0-9]{1,4}", value) else value',
     'return value',
     "test_nt_numeric_and_prefixed_inputs_normalize_without_changing_output", "Invalid POA"),
    ("allow unmatched codes", 'if missing:\n            raise ValueError(f"Unmatched {geo}',
     'if False:\n            raise ValueError(f"Unmatched {geo}',
     "test_all_or_partially_missing_inputs_fail_before_export", "ValueError not raised"),
    ("allow mixed-state inference", 'if len(candidate_states) > 1:',
     'if False:',
     "test_direct_full_table_ambiguity_requires_explicit_state", "ValueError not raised"),
    ("skip format collision preflight", 'if conflicts:\n        raise FileExistsError',
     'if False:\n        raise FileExistsError',
     "test_format_conflict_is_reported_before_any_source_access", "AssertionError: source accessed"),
    # Its target test is skipped as root, so run mutations as a regular user.
    ("skip read-only output preflight", 'if read_only:\n        raise PermissionError',
     'if False:\n        raise PermissionError',
     "test_read_only_manifest_is_refused_before_source_access", "AssertionError: source accessed"),
    ("treat one-sided missing values as equal", 'equal.fillna(False).all()',
     'equal.fillna(True).all()',
     "test_split_different_numeric_values_and_missingness_rejected", "ValueError not raised"),
    ("skip existing data mode copy", '                shutil.copymode(target, pending)',
     '                pass  # mutation: skip data mode copy',
     "test_data_file_mode_survives_replacement_under_distinct_umasks", "data permission mode was not preserved"),
    ("skip rollback restore", '                    backups[target].replace(target)',
     '                    pass  # mutation: do not restore published data',
     "test_second_publish_failure_rolls_back_first_table_and_provenance", "published bytes changed after rollback"),
    ("skip recovery journal preflight", 'if recovery_journals:', 'if False:',
     "test_retained_recovery_journals_block_before_source_even_when_malformed", "AssertionError: source accessed"),
    ("skip missing root termination", 'if parent == existing_parent:', 'if False:',
     "test_missing_root_parent_walk_terminates_before_source_access", "parent walk exceeded bound"),
    ("skip rollback journal update",
     '        try:\n            save_recovery()\n        except BaseException as error:\n            journal_error = error',
     '        try:\n            pass\n        except BaseException as error:\n            journal_error = error',
     "test_partial_rollback_journal_tracks_each_file_and_blocks_new_exports", "rollback journal state was not updated"),
    ("silence leftover journal warning", '        warnings.warn(leftover, RuntimeWarning, stacklevel=2)',
     '        pass  # mutation: silent leftover',
     "test_successful_export_warns_when_its_recovery_journal_cannot_be_removed", "RuntimeWarning not triggered"),
    ("remove journal last during cleanup", '    _remove_temporary(work / "recovery.json")',
     '    pass  # mutation: journal not removed first',
     "test_interrupted_cleanup_removes_journal_before_other_staging_files", "journal outlived interrupted cleanup"),
    ("skip orphan staging warning", 'if orphan_folders:', 'if False:',
     "test_journal_less_staging_folders_are_reported_and_kept", "RuntimeWarning not triggered"),
    ("allow invalid numeric measures", 'if invalid.any():', 'if False:',
     "test_unexpected_numeric_tokens_rejected_with_context", "ValueError not raised"),
    ("trust damaged cache", 'if dest.is_file():\n        _validate_zip(dest)',
     'if dest.is_file():\n        pass',
     "test_cache_corruption_is_rejected_offline_without_replacement", "BadZipFile not raised"),
    ("bypass output lock", 'with _export_lock(out_dir):',
     'with tempfile.TemporaryDirectory():',
     "test_lock_contention_and_process_death_release", "AssertionError: source accessed"),
    ("omit retained source hash", 'retained.setdefault("source_sha256", None)',
     'retained["source_sha256"] = None',
     "test_manifest_hashes_identify_each_export_not_latest_for_all", "AssertionError: None !="),
    ("merge split metadata parts incorrectly", 'parts.update(pattern.fullmatch(Path(p).name).group(1).upper() for p in paths)',
     'parts.update(pattern.fullmatch(Path(p).name).group(1).upper() for p in paths[:1])',
     "test_discovery_shared_and_inline_rich_strings_without_loading_data", "AssertionError: Lists differ"),
    ("drop lettered profile metadata", '"sequential": r["Sequential"]} for r in variables]',
     '"sequential": r["Sequential"]} for r in variables if re.fullmatch(r"G\\d{2}", r["Profile table"])]',
     "test_lettered_profiles_sharing_one_datapack_part_are_retained", "Metadata missing for table parts"),
    ("scan full archive on cache hit", '        _validate_zip(dest)',
     '        _validate_zip(dest, full=True)',
     "test_reused_and_explicit_zip_checks_do_not_decompress_unselected_members", "AssertionError: full scan"),
    ("report unsupported locking as busy", 'if exc.errno in busy:', 'if True:',
     "test_lock_support_and_permission_failures_are_not_reported_busy", "unsupported locks were reported as busy"),
    ("skip table ZIP read diagnostics", 'with _zip_read_errors(source), ZipFile(source) as z:',
     'with ZipFile(source) as z:',
     "test_compressed_table_corruption_names_archive_in_api_and_cli", "zlib.error"),
    ("skip metadata ZIP read diagnostics", 'with _zip_read_errors(archive):',
     'with warnings.catch_warnings():',
     "test_compressed_metadata_corruption_names_archive_for_discovery", "zlib.error"),
    ("ignore selected year in core reader", 'df = _combine_validated_table(z, table, geo, state, year=year)',
     'df = _combine_validated_table(z, table, geo, state, year=2021)',
     "test_explicit_year_changes_source_header_values_and_inventory", "Table G01 not found"),
    ("skip explicit-source year/profile preflight", '        _validate_source_identity(Path(source_zip), year, geo=geo)',
     '        pass  # mutation: source identity deferred past mkdir',
     "test_wrong_year_profile_and_mixed_sources_do_not_create_export_folder", "AssertionError: True is not false"),
    ("skip member header checks", '                with archive.open(info):\n                    pass',
     '                pass',
     "test_encrypted_or_unsupported_members_are_refused_before_reading", "AssertionError: data read"),
    ("skip cached identity validation", '                _check_archive(archive, year, geo=geo, suggest_year=False)',
     '                pass  # mutation: trust cached identity',
     "test_cached_identity_is_checked_at_resolution_with_recovery_and_path", "ValueError not raised"),
    ("omit source path from identity error", 'raise ValueError(f"{source}: {error}") from error',
     'raise ValueError(str(error)) from error',
     "test_explicit_resolution_identity_error_includes_source_path", "source path missing"),
    ("leak vintage code column in CLI", 'code = _code_column(args.geo, args.year)',
     'code = _code_column(args.geo, 2021)',
     "test_cli_discovers_2016_but_does_not_export_it", "code column leaked"),
    ("use wrong 2016 national token", 'frozenset({"AUS"})', 'frozenset({"AUST"})',
     "test_all_states_and_national_tokens_for_both_years", "Invalid national member token"),
    ("accept nonintegral year types", 'not isinstance(year, Integral) or year not in _VINTAGES',
     'year not in _VINTAGES',
     "test_year_and_geography_validation_precedes_every_source_access", "AssertionError: source accessed"),
    ("skip download identity before cache publication", '        if validate is not None:\n            validate(tmp)',
     '        pass  # mutation: publish unidentified download',
     "test_wrong_download_identity_never_enters_cache_and_retry_succeeds", "ValueError not raised"),
    ("skip destination preflight before explicit ZIP",
     '        _preflight_destination(\n' + _DESTINATION_ARGUMENTS,
     '        pass  # mutation: skip early destination inspection',
     "test_destination_refusals_precede_every_explicit_zip_access",
     "source or lock accessed before refusal"),
    ("skip locked destination reread",
     '        manifest_path, targets, retained_tables = _preflight_destination(\n'
     + _DESTINATION_ARGUMENTS,
     '        pass  # mutation: trust early destination inspection',
     "test_destination_journal_is_rechecked_under_lock_after_explicit_validation",
     "AssertionError: source resolved"),
    ("duplicate orphan warnings",
     '    orphan_folders = [path for path in orphan_folders if path not in warned_orphans]',
     '    pass  # mutation: warn twice',
     "test_orphan_warning_is_emitted_once_across_destination_preflights",
     "AssertionError: 2 != 1"),
    ("inspect before an existing lock",
     '                         and not os.path.lexists(out_dir / _LOCK_NAME))',
     '                         and True)  # mutation: ignore existing lock file',
     "test_running_export_is_busy_before_its_staging_or_the_zip_is_inspected",
     "source accessed while busy"),
    ("name deleted download temporary", '        except BadZipFile as error:\n            # The temporary',
     '        except NotImplementedError as error:\n            # The temporary',
     "test_corrupt_download_names_cache_path_not_deleted_temporary_file", "temporary path named"),
]


def main() -> int:
    try:
        import pandas  # noqa: F401
        import requests  # noqa: F401
    except ImportError as exc:
        print(f"Mutation setup failed: {exc}. Use the project .venv Python and install "
              "the README dependencies before running mutations.", file=sys.stderr)
        return 2
    original = (ROOT / "abs_census_etl.py").read_text()
    caught = 0
    attempted = 0
    with tempfile.TemporaryDirectory(prefix="abs-etl-mutations-") as td:
        scratch = Path(td)
        (scratch / "tests").mkdir()
        shutil.copy2(ROOT / "tests/test_abs_census_etl.py", scratch / "tests/test_abs_census_etl.py")
        shutil.copy2(ROOT / "tests/test_data_features.py", scratch / "tests/test_data_features.py")
        shutil.copy2(ROOT / "tests/test_year_coverage.py", scratch / "tests/test_year_coverage.py")
        for name, before, after, target, reason in MUTATIONS:
            if name == "skip existing data mode copy" and os.name != "posix":
                print(f"SKIPPED: {name} (POSIX permission modes)")
                continue
            attempted += 1
            if original.count(before) != 1:
                raise RuntimeError(f"Mutation anchor must be unique: {name}")
            (scratch / "abs_census_etl.py").write_text(original.replace(before, after, 1))
            # Avoid timestamp/size-based bytecode reuse between module variants.
            shutil.rmtree(scratch / "__pycache__", ignore_errors=True)
            run = subprocess.run([sys.executable, "-B", "-m", "unittest", "discover",
                                  "-s", "tests", "-k", target, "-v"],
                                 cwd=scratch, capture_output=True, text=True)
            output = run.stdout + run.stderr
            intended = ((f"FAIL: {target}" in output or f"ERROR: {target}" in output)
                        and reason in output and "Ran 1 test" in output
                        and "SyntaxError" not in output and "ImportError" not in output)
            if run.returncode and intended:
                caught += 1
                print(f"CAUGHT: {name} -> {target} ({reason})")
            else:
                print(f"NOT CAUGHT AS INTENDED: {name}\n{output}")
    print(f"{caught}/{attempted} mutations caught for the intended reason")
    return 0 if caught == attempted else 1


if __name__ == "__main__":
    raise SystemExit(main())
