# Project working agreement

## Workflow

Use [SABRE v0.7](https://gist.github.com/Co0olCat/c977dcfe3e365224dc8c2c75bc25fe3d):
"One agent builds. Another agent challenges the uncommitted Git delta. The human owns scope, risk, and handoff."

- Keep exactly one progress file per day at `docs/progress/YYYYMMDD.md`, using the current date. Put all slices, implementation plans, findings, checks, and handoffs for that day in that file; do not create per-day directories or separate slice files.
- Before implementation, record each slice's goal, why, affected files, non-goals, acceptance criteria, risk class, and reviewer focus. Record uncertainties as open questions.
- The user handles independent review with another model. Do not spawn internal reviewer agents unless explicitly requested. Prepare plans and actual deltas for that external review. Keep human scope decisions explicit; a direct instruction to implement a scoped slice authorizes that slice.
- Implement one authorized slice at a time. The user's independent reviewer inspects the actual uncommitted delta, including staged and untracked files, rather than relying on the builder's summary.
- Record findings, fixes, checks, reviewer verdict, residual risks, and handoff. Prepare the full current delta for the user's re-review after fixes. Tests passing is not human acceptance.
- The human decides acceptance and handoff. The user controls builder/reviewer role assignments across models.
- Do not commit, push, or publish unless explicitly asked. Existing staged files belong to the user; do not stage, unstage, or overwrite unrelated changes. Change staging only with explicit user authorization, recording the paths and reason in the daily progress file.

## Current scope and decisions

See `docs/progress/20261019.md` for the GitHub distribution plan.
Use `2000` / `POA2000` in examples and relevant fixtures, replacing `2084`.
Distribute through GitHub Releases, not PyPI. The same Windows or Linux/macOS setup script installs and updates; do not add an application update command.

## Checks

Use the project Python: `.venv/bin/python -m unittest discover -s tests -v` on Unix, or `.venv\Scripts\python.exe -m unittest discover -s tests -v` on Windows.
Keep regression tests offline, with realistic ABS filenames, headers, and geography-code formats. Preserve leading zeros and string codes. Use real NSW 2000/2010 and NT 0800/0810 identifiers with the correct state; use deliberately bare NT fixtures to test numeric inference. Test partial unmatched-code failures and input case/whitespace normalization.
