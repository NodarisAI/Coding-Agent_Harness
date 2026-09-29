## What changes for the person using the harness

<!-- One or two sentences in plain English. -->

## Why

<!-- The problem, issue or decision this addresses. -->

## How it was tested

<!-- The command you ran and its result, for example: NODARIS_HARNESS_NO_BG=1 python3 -m pytest (exit 0, N passed). -->

## Checklist

- [ ] Branch is named `feat/`, `fix/`, `docs/` or `chore/` with a short topic, and this pull request holds one change.
- [ ] Tests added or updated for every behaviour change, and the full suite passes.
- [ ] The engine still imports only the standard library and runs on Python 3.9.
- [ ] Product text follows `packs/core/skills/plain-copy/SKILL.md`.
- [ ] No secrets, real patient data or customer data in code, fixtures, logs or this description.
- [ ] No AI authorship marks in commits, code, comments or this description.
- [ ] If the policy, guards, approvals, redaction or security check changed: a threat note is below and the policy version is bumped where needed.
- [ ] `CHANGELOG.md` updated if this is user-visible.

## Threat note (sensitive changes only)

<!-- What could go wrong, who is affected, and which test proves it cannot. -->
