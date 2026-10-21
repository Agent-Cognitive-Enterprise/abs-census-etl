# Slice 12 reader regression snapshots (checkout only)

These `.py.txt` files retain the exact one-off sources used for genuine-data
checks. They are review records, not supported commands, and are not shipped in
release archives. They depend on ignored local evidence not present in another
clone. **Do not rename and rerun historical snapshots:** they write reports into
the paths embedded in their source, which may already be sealed evidence.
Assertions require ordinary Python without `-O` or `PYTHONOPTIMIZE`.

| Snapshot | Recorded output directory under docs/evidence/ |
| --- | --- |
| check_real_offline.py.txt | 20261020-slice12 |
| r1_check_real_offline.py.txt | 20261020-slice12-r1 |
| r1_check_all_state_poa.py.txt | 20261020-slice12-r1 |
| r2_check_real_offline.py.txt | 20261020-slice12-r2 |
| r2_check_all_state_poa.py.txt | 20261020-slice12-r2 |
| r3_check_real_offline.py.txt | 20261020-slice12-r3 |
| r3_check_all_state_poa.py.txt | 20261020-slice12-r3 |

The initial and R1 snapshots moved here byte-for-byte from the slice 11 research
area in R2 for findability. The daily progress record anchors their hashes and
separately identifies the final application hash tested in each round.

Full-pack checks exercise genuine 2016 NSW POA loading of all 59 tables,
15,535 metadata variables, all 110 header mappings, POA2000/2010 population/age
references and source cells for both G04 parts. They also check default/explicit
2021 equality, discovery, G04 joining and exports for all ten retained complete
2021 NSW geography packs. The all-state script separately runs actual application
identity, member selection, state inference and wrong-year refusal on all ten
saved genuine 2016 POA inventories. It verifies input manifests against external
hashes, and missing payload ranges are errors, never fabricated bytes.

No script downloads data. All-state listing checks do not imply full payload or
metadata fidelity validation, or full ZIP hashes, for every state. Normal offline
fixture regressions remain in tests/test_year_coverage.py. Slice 11's research
audit tests still run through the existing checkout test bridge; these historical
assertion scripts are separate local evidence, not an additional CI test suite.
