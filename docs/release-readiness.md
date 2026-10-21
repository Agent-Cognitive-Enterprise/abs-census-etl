# Release handoff — 0.2.0 candidate, 2026-10-20

This checkout-only snapshot is excluded from application archives. Refresh it
before a later release. Publication results belong in the daily progress record;
a candidate checklist is not proof that its release workflow passed.

Status: 0.2.0 was published on 2026-10-20 (publish run 39372369628; tag on
bdd5d88), as recorded in docs/progress/20261020.md. The checklist below is kept as
that release's record. The current source is development version 0.3.0.dev0; see
the final section.

## Scope and authority

The owner signed off slices 7–10 and requested “Publish next release”. That
explicitly authorizes preparation and publication of 0.2.0 through the existing
manual workflow. The accepted implementation is commit
42c5abfa83dd5f470285f896eae514348d69fec8. Release preparation changes metadata and
documentation, not runtime, installers, tests, workflows or dependency pins.

Version 0.2.0 adds ten supported 2021 GCP geographies, searchable table/variable
metadata, strict numeric validation, source hashes and cooperating-writer locks.
It does not add historical Census years. GitHub Releases remains the distribution
channel, with MIT licensing, CSV by default and optional Parquet.

The managed target remains standard Python 3.14, native Linux x86-64/ARM64 with
glibc 2.28+, macOS Intel/ARM64 12+, and Windows x64 10 (1803+)/11. Minimum versions,
enterprise endpoint/proxy policies and arbitrary filesystem/power-loss behavior
are not established by a newer wheel alone.

## Evidence to inspect

The preceding [v0.1.0 release workflow](https://github.com/Agent-Cognitive-Enterprise/abs-census-etl/actions/runs/39364324272)
passed all five hosted targets. Its publication/public-install results and the
subsequent slices 7–10 verification are recorded in docs/progress/20261020.md.
Those previous checks do not certify the 0.2.0 tag. Full local/native logs and
hash manifests are retained locally under ignored docs/evidence/ directories;
they are not public CI artifacts or shipped application files.

The accepted implementation's most recent local record reports 220 unit tests
(two platform skips), 28 intended mutation failures caught, packaging checks and
genuine ABS checks on 11 packs. Default discovery covers 16,984 variables across
119 CSV parts per pack. Four curated QuickStats references have retained HTML
snapshots. R3's encrypted/unsupported-member checks were revalidated on real
packs locally, but R3 did not claim new native Windows/macOS results.

For this release, require fresh local validation and all hosted matrix jobs on
the tagged source before publication. The Release workflow runs Ubuntu x64/ARM64,
macOS ARM64/Intel and Windows x64, including restricted-token Windows tests and
PowerShell 5.1/7 installer smokes. A restricted token is not a separately
provisioned standard-user profile. Uploaded log artifacts have 14-day retention;
that is not the Actions run-log retention setting.

## Candidate procedure

1. Verify version 0.2.0 in pyproject.toml and the root uv.lock entry; all other
   dependency records/hashes must be unchanged. Review the changelog, module URLs,
   license and installer bytes. Retain current dependency pins and document age
   review, as directed; do not invent a new waiting-period policy.
2. Run the [development checks](development.md#tests), including mutations and
   packaging with Parquet. Do not sync or clear the user's existing environment
   just to perform release validation; build/install probes use disposable venvs.
3. Build a licensed bundle in an absent output folder:

   ```bash
   .venv/bin/python scripts/release.py build --tag v0.2.0 --assets dist/candidate-0.2.0
   .venv/bin/python scripts/release.py verify --tag v0.2.0 --assets dist/candidate-0.2.0
   ```

4. Commit/push the scoped release preparation, create a new annotated v0.2.0 tag
   and push it. Never replace an existing tag or release asset.
5. Dispatch **Release from the default branch**, supplying **v0.2.0 as the tag
   input**, with publish=false. Do not select the tag in the workflow-ref selector:
   that would skip validation. Inspect every job and retain source/bundle hashes.
6. Only after validation succeeds, dispatch the existing workflow from the default
   branch with tag=v0.2.0 and publish=true. Owner authorization is recorded above;
   no required-reviewer environment/repository policy is changed. Publication
   verifies assets and refuses existing releases/drafts. See the
   [release contract](release-assets.md#build-validation-and-manual-publication)
   for failure handling; do not silently overwrite a partial publication.
7. Inspect the published regular/latest release and exact five assets. Anonymous
   downloads must match the publish workflow's tested bundle. Test both direct
   module URLs and fresh managed bootstrap/update commands in disposable homes,
   including update from 0.1.0 with saved Parquet choice and user data retained.
   Where practical, repeat on the authorized native Windows host. Remove private
   test environments after collecting full logs and hashes.
8. Record actual run IDs, publication outcome, public-download checks and limits
   in the daily progress file; a later progress-only commit must not move the tag.

The [README](../README.md) is the user entry point. The
[installation guide](installation.md), [data guide](data-guide.md) and
[release contract](release-assets.md) provide detailed procedures. No publication
or hosted validation is claimed merely by preparing this candidate document.


## Unreleased year-coverage handoff

The development version is 0.3.0.dev0; this is not a published release.
The working source extends GCP to explicit 2016 across the ten verified geography
types, with year-qualified exports and optional revision/boundary provenance.
Published 0.2.0 assets still represent 2021 support; these changes do not publish
new assets. Before tagging, rerun unit, mutation, packaging, actual v0.2.0 writer
compatibility and year-aware genuine-data checks, refresh the dated native
records, and review the full delta. Also replace every 0.3.0.dev0 and
"unreleased"/"development version" reference with the release version: the
README intro, earlier-Census and Setup sections, the data guide intro and Census-years
section, the development guide's native validation record, the CHANGELOG
Unreleased heading/entry, pyproject.toml and uv.lock. See
the current daily progress record for measured results, evidence limits and the
user's acceptance status.
