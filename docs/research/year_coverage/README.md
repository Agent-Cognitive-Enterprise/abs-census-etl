# Bounded 2016 header and metadata validation (checkout only)

This research check reuses the externally anchored slice 11 evidence and fetches
only missing compressed blocks when explicitly invoked with --acquire. It checks
all 110 CSV headers and the complete 15,535-variable workbook in each of the ten
2016 NSW geography packs, plus POA NT. It runs the actual table reader for G01,
G02 and G04 and compares G04 cells independently with stdlib CSV extraction.
POA0800, SA2 101021007 and LGA17200 have saved independent QuickStats references;
NSW POA2000/2010 are also checked by the full-pack maintainer regression command.

Acquisition requires a new evidence directory and a separately approved budget.
Per-source caps total 112 MiB: 40 MiB for SA1 and 8 MiB each for the other nine
sources. Errors and redirects count. No whole national packs are downloaded.
Actual response bodies, URLs, status, range, time, size and SHA-256 are ledgered
by the tested slice 11 HTTP helper. Original inputs are manifest-verified before
reuse. An archive size change is refused rather than silently mixing revisions;
selected complete members also receive CRC and SHA-256 checks. Partial ZIP ranges
do not establish a full archive hash or every table's payload fidelity.

```sh
.venv/bin/python docs/research/year_coverage/check_ranges.py \
  --acquire docs/evidence/NEW-year-range-directory
.venv/bin/python docs/research/year_coverage/check_ranges.py \
  --verify docs/evidence/RECORDED-directory --expect-sha256 RECORDED-MANIFEST-HASH
.venv/bin/python -m unittest discover -s docs/research/year_coverage -v
```

Use the actual external hash in the daily progress record; never replace the
manifest just to make an input verify. Offline replay checks every response body,
per-source ledger sum/cap and complete manifest inventory, refuses any gap and
recomputes the table/header results. A runtime-hash mismatch is an error rather
than a claim that an old report describes new code. Raw evidence is ignored and
not present in another clone. Acquisition never overwrites existing evidence.

The synthetic range tests run from the regular checkout packaging-test bridge,
including future checkout CI. They are skipped only in source distributions,
which deliberately exclude research tools. No hosted run is implied before the
user commits/pushes this delta. Shipped tests/check_abs_data.py checks full local
packs instead and supports --offline, --year and all-geography selection.


`original_acquisition.py.txt` is the exact initial acquisition source. The current
entry point additionally reparses the anchored saved QuickStats pages during
replay and normalizes Python tuple/list representation before comparing JSON
reports. These changes do not rewrite original responses or the report. Keep the
external manifest hash from the progress record; do not rerun acquisition against
an existing directory to update a code hash.

`all_state_runtime.py.txt` is the exact one-off source for the final offline
100-pair runtime-selection report. It is an audit snapshot, not a supported
command: it assumes the repository root and retained evidence paths, uses plain
Python assertions (do not run with -O), and writes into its recorded report path.
It validates source manifests and selection/inference, not all member payloads.

To test repaired runtime code against the same sealed bytes, add
`--revalidate-report /path/outside/the/bundle/new-report.json` to `--verify`.
The external manifest hash remains required; all source results must match.
Only the runtime hash may differ. The new report is created exclusively, and
neither responses nor the original report are rewritten. This is a new-code
validation, distinct from reproducing the original report's runtime hash.
