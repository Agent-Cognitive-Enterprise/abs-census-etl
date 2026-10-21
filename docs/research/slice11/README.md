# Slice 11 evidence audit (checkout only)

These files make the source reconnaissance reviewable. They add no runtime year
support and are excluded from the explicitly listed release source archive.
The raw data remains in ignored local evidence directories; this is not a claim
that another clone contains those bytes.

`original/*.py.txt` are exact historical snapshots of the scripts that made
network requests or produced the first claims: the three original reconnaissance
scripts, `matrix.py.txt` (the `matrix.py` that ran the all-state survey, before
its offline `--verify` mode was added) and `20261020-slice11-supporting.py.txt`
(the one-off probe that fetched the three supporting pages). They show how the
claims were produced, including the original error labels and lossy README
decoding. They are records, not commands: **do not rename and run them**. The
old acquisition scripts overwrite files and are unsafe for rerunning against the
original evidence directory. Their historical outputs have been preserved rather
than rewritten to hide those defects.

`evidence.py` is the supported audit entry point. It uses only the standard
library, contains no network operations and does not import the application.
Use it from the repository root:

```sh
.venv/bin/python docs/research/slice11/evidence.py verify \
  --evidence docs/evidence/20261020-slice11 \
  --expect-sha256 f28c9925be9d821c2e3af628cc758816a642fdf8826b7fb6a0c8b9ca40789890
.venv/bin/python docs/research/slice11/evidence.py replay \
  --evidence docs/evidence/20261020-slice11 \
  --expect-sha256 f28c9925be9d821c2e3af628cc758816a642fdf8826b7fb6a0c8b9ca40789890 \
  --output docs/evidence/20261020-slice11-new-audit
.venv/bin/python docs/research/slice11/evidence.py verify \
  --evidence docs/evidence/20261020-slice11-new-audit
.venv/bin/python -m unittest discover -s docs/research/slice11 -p 'test_*.py' -v
```

Replay verifies the original 210-file manifest before reading saved HTTP ranges. A gap
in those ranges is an error, never a download or a zero-filled substitute. It re-
extracts source population cells, parses the saved independent QuickStats HTML and lists
complete ZIP inventories. README decoding uses UTF-8 with BOM first, then strict UTF-8,
otherwise inferred cp1252 decoded strictly; ASCII is labelled explicitly as the UTF-8
subset. No replacement-character decoding is used. ZIP timestamps are recorded without
an assumed timezone or publication date. The derived ledger labels HTTP 404 as HTTP 404
and preserves the original error. It reports unknown browser response sizes and does not
certify the total budget.

Replay requires a new output directory and refuses to overwrite an old report.
A new manifest is generated there automatically. `verify` actually checks the
specified manifest's file lengths and SHA-256 values. To generate a manifest for
another retained audit directory, use `manifest --evidence DIRECTORY --manifest
DIRECTORY/manifest.json`; that file must not already exist. Hashes establish byte
consistency, not independent authenticity or completeness of unrecorded bytes.
No command changes the source ledger, raw responses or existing manifests.

The review record in `docs/progress/20261020.md` contains the support proposal,
source origins, recorded owner decisions and limitations. The original acquisition
code and new utility are deliberately checkout-only: no packaging allow-list or
user-facing API is changed by this audit repair.

The manifest inventory rejects unlisted files. As with the original manifest,
only `.pyc` files directly inside the evidence root's `__pycache__/` are
excluded. Nested caches, other files and raw response files have no exemption. Symlinks
are refused. Use `--expect-sha256` with a hash from the independently reviewed
progress record to detect a replaced manifest as well as changed data. Without
that external anchor verification establishes internal consistency only.
Replay output must be outside the input evidence tree, including after resolving
symlinks, and must not exist. Report counts separate actual population comparisons
from observations with no independent QuickStats reference.

Research audit tests run explicitly in CI's checkout-only research step, using
the command above. Product `unittest discover -s tests` does not require this
folder. Offline synthetic tests work in a clean clone without docs/evidence/;
replaying real acquisitions requires the separately retained, ignored evidence.
Historical snapshots are retained as audit records, not advertised as standalone
replay commands. No hosted success is implied until the relevant workflow runs.

## All-state structural survey (slice 11 only)

The approved acquisition cap is 128 MiB (134,217,728 response-body bytes), fresh
for the all-state phase. `matrix.py` checks all 100 combinations of ten 2016 GCP
geographies and NSW/VIC/QLD/SA/WA/TAS/NT/ACT/OT/AUS. It can reuse the original
hash-anchored twelve observations offline; it saves every new range response,
status, request URL/time and byte count. Errors and redirects count too. An
ignored range is bounded rather than turned into a full-pack download. Existing
output directories are refused. Unknown/unavailable endpoints remain unresolved;
no 404 is silently converted into an absence claim.

Acquisition is optional and networked. Do not run it just to verify retained
results, or it starts a new transfer phase requiring its own budget:

```sh
.venv/bin/python docs/research/slice11/matrix.py \
  --prior docs/evidence/20261020-slice11 \
  --output docs/evidence/NEW-matrix-directory
```

The recorded result can be independently replayed entirely offline:

```sh
.venv/bin/python docs/research/slice11/matrix.py \
  --prior docs/evidence/20261020-slice11 \
  --verify docs/evidence/20261020-slice11-all-state \
  --expect-sha256 d8ac1529c12e6a46508e0f012a58bde094504f4bf5ea6dd2ef0dbf1e769825f9
.venv/bin/python docs/research/slice11/supporting.py --verify \
  --matrix docs/evidence/20261020-slice11-all-state \
  --matrix-hash d8ac1529c12e6a46508e0f012a58bde094504f4bf5ea6dd2ef0dbf1e769825f9 \
  --support docs/evidence/20261020-slice11-all-state-supporting \
  --support-hash f99228f1dda5d93f24bff72913c817887a68fd212b74a2244ba170cf2c4aac95 \
  --prior docs/evidence/20261020-slice11
```

Matrix verification replays every cell's central directory, G01 header/sample,
metadata names and readmes from retained bytes and refuses missing ranges. It
checks the complete pair set, response ledger, declared cap and external hashes.
It also requires every cell to list exactly the data parts of the complete POA
NSW archive (110 parts, 59 tables), and reads the complete, CRC-checked G01 of
the OT CED, SED, LGA, POA and SA4 packs against `OT_G01_ROWS`. Those packs hold
mostly special codes; `matrix.py` names them from the geography workbook.
Supporting verification checks the combined budget and saved POA0810/POA2010
population references, and decodes the historical ASGS page using its declared
HTTP charset. Its optional `--acquire` mode uses only the matrix phase's remaining
budget in a new output directory; do not repeat acquisition against retained
bundles. Use the recorded hashes rather than generating replacements for inputs.

All 100 actual endpoints were present in this run, so no geography exception is
claimed. The survey is structural evidence, not a full metadata inventory, full
archive CRC check or demographic fidelity proof for every table. Full ZIP hashes
are available only for the original complete POA NSW pack. No 2016 application
support is added by these tools. Raw evidence remains local/ignored; no hosted
execution is claimed before these sources are committed and pushed.


## Slice 12 reader regressions

The historical and repair audit snapshots now live beside their own
[README in docs/research/slice12](../slice12/README.md). Their bytes are unchanged
by the move. Slice 11 acquisition/audit commands above remain unchanged.
