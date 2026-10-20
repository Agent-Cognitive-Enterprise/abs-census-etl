# Release handoff — snapshot of 2026-10-20

This is a **checkout-only, historical snapshot**, excluded from application
archives. Refresh version, evidence and policy status before each release.

This records implementation and validation status. It is not authorization to publish, a
hosted-CI result, or a guarantee that every supported target works. The owner signed off
all six implementation slices and delegated preparation/publication of 0.1.0.
This snapshot is prepared before that publication; its outcome belongs in the
daily progress record.

## Distribution snapshot

- GitHub Releases only; no PyPI publishing workflow.
- Release candidate version: `0.1.0`; the release tag must match project metadata.
- MIT license, with the same `LICENSE` in source and wheel archives.
- CSV by default; Parquet is an explicit install and export option.
- Fresh download of the same installer command handles installation and updates.
- Managed target: standard Python 3.14, native Linux x86-64/ARM64 glibc 2.28+,
  macOS Intel/ARM64 12+, and Windows x64 10 (1803+)/11.

The [README](../README.md) is the beginner entry point. See
[installation and removal](installation.md), [API and export recovery](data-guide.md),
[development and validation](development.md), and the
[release asset contract](release-assets.md).

## Validation evidence

The daily record `docs/progress/20261020.md` in the Git checkout records the slice-5
review rounds, tested revisions, results and limitations. That internal progress record
is not included in the app archives. Full raw logs and hash manifests are local ignored
artifacts under `docs/evidence/`; they are not public evidence links and cannot be
retrieved from a source download. Ask the maintainer for retained logs, or reproduce the
checks below.

| Recorded round | What actually ran | Limits |
| --- | --- | --- |
| Slice 5 initial implementation | Native Linux x64, macOS ARM64 and Windows x64; installer first/repeat/update/failure checks | These results precede later release-tool/license edits |
| Slice 5R1 | Linux unit/packaging/mutations and real installer smoke; Windows standard-token unit suite; full installer smoke on PowerShell 5.1 and 7 (46 assertions each); PowerShell 7 offline checks (177 assertions) | Windows stages ran separately; not a hosted checkout; no new macOS run |
| Slice 5R2, MIT license and residual fixes | Linux 185 tests (2 platform skips), 17 release tests also under optimization, packaging, 17 mutations, licensed bundle build and real Linux installer smoke | No native Windows/macOS rerun after these changes; rewritten Windows elevation step remains unverified natively |
| Configured GitHub Actions matrix | Ubuntu x64/ARM64, macOS ARM64/Intel and Windows x64 | Configuration only; no hosted run recorded |

These are historical results, not claims about a later checkout. Hosted CI
[subsequently passed all five native targets](https://github.com/Agent-Cognitive-Enterprise/abs-census-etl/actions/runs/39361189657)
on ce34be5, superseding the earlier configured-only target and no-hosted-run
limitations below; release 0.1.0 still needs its own validation. Slice 6's own checks
are recorded in docs/progress/20261020.md at handoff. Linux ARM64 and Intel macOS now have hosted results in the linked CI run. Testing with a restricted Windows token is not testing
an independently provisioned standard-user profile. Enterprise proxy/TLS/endpoint
policies, minimum OS versions and all storage or power-loss conditions are not
established by the recorded checks.

### Reproduce validation from a checkout

Use the [locked development setup](development.md#platforms-and-development) with
Parquet so tests do not silently omit that feature. Then follow the
[offline tests and native smokes](development.md#tests). Packaging and installer
smokes use the network; the ETL fixture tests do not download ABS DataPacks.

Build a fresh bundle and verify it from the project directory (use the actual project
version if it changes). Pick an absent output directory:

```bash
.venv/bin/python scripts/release.py build --tag v0.1.0 --assets dist/candidate
.venv/bin/python scripts/release.py verify --tag v0.1.0 --assets dist/candidate
.venv/bin/python tests/check_installer.py --release-dir dist/candidate
```

On native Windows, substitute `.venv\Scripts\python.exe` and use
`tests/check_windows_installer.py --release-dir dist/candidate` for the smoke. Use
`--powershell` with a PowerShell 7 executable for its full smoke. Run Windows checks as
a normal user with a private profile TEMP; the restricted-token CI wrapper is for
elevated hosted runners. Keep output logs with the commit ID, source/bundle hashes, OS,
architecture, interpreter and shell versions. A Linux run cannot establish Windows/macOS
behavior. Rebuild after documentation changes because README and supporting documents
are source-archive inputs.

## Before the first public release

1. Review and commit the accepted source at the owner's direction. Set matching
   project/lock versions and create a new canonical tag; never move a release tag.
   Before tagging, refresh this handoff and the dated **Recorded native
   validation** section of `development.md`, which ships in the source archive.
2. Use the existing explicit manual dispatch process for this delegated release.
   No required-reviewer environment is configured. The broader future policy
   remains an owner decision; no repository setting is changed here.
3. Review dependency/build-tool changes and upload ages. The owner chose current
   pins and documented review, without an enforced waiting period.
4. Confirm repository Actions access, runner availability and minutes. Run the
   manual **Release** workflow **from the default branch** (the “Use workflow
   from” selector), with the existing tag supplied in the **tag input** and
   `publish: false`. Do not select the tag as the workflow ref: the prepare job
   would skip. Inspect every native job and retain its source/bundle hashes
   and logs. Uploaded log artifacts have 14-day retention; this is not the
   Actions run-log retention setting.
5. Resolve hosted failures and validate the current licensed bundle on Windows
   and macOS, including the changed Windows elevation probe. A prior native run
   is not evidence for a later revision.
6. Only after owner authorization, dispatch **from the default branch** with
   the selected **tag input** and `publish: true`. Publication
   checks names/digests and refuses existing releases/drafts; see the
   [partial-publication guidance](release-assets.md#build-validation-and-manual-publication).
   A development/prerelease tag will not provide a stable `releases/latest` route.
7. After an actual stable release, test both README download commands against
   GitHub's real application assets in isolated accounts/homes, including repeat
   installation, update and failure retention. Local `--release-dir` smokes do
   not prove the application's published download path.

No public release, real upload, hosted workflow run or repository-setting change is
performed by this documentation slice. Human acceptance of code and actual publication
remain separate actions.
