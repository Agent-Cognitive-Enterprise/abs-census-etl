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
MUTATIONS = [
    ("dtype uses stripped header", 'dtype={raw_code_column: "string"}',
     'dtype={code: "string"}',
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
    ("allow unmatched codes", 'if missing:\n            raise ValueError',
     'if False:\n            raise ValueError',
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
