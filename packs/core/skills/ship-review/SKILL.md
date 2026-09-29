---
name: ship-review
description: Use before any push, pull request, merge, release or deploy, and when the person says ship it, land it, open a PR or get it green. Runs an independent review of the exact commit, fixes what it finds, records the result against that commit, and only then asks for the push approval the harness requires.
---

# Ship review: an independent review of the exact commit

Pushes, publishing and deploys need a person's one-time approval under the rules of engagement. The approval
request shows the review state of the commit being pushed, so the person approves knowing whether it was reviewed.
A review is bound to one commit and its tree: any new commit makes it stale.

Adapted from the review and shipping parts of pstack (MIT), rebuilt around the harness's commit-bound review
record.

## 1. Finish the work first
- Commit the change. The review covers only what is committed.
- Run the repository's verify command and quote its exit code. A red verify stops here: fix it first.

## 2. Pre-mortem (releases, deploys and anything a customer will use)
Assume it failed 90 days from now. Write five failure stories specific to this change and this customer, not
generic ones: for example a practice's file shape the parser never saw, a tenant seeing another tenant's rows, a
number that silently changed, an alert nobody receives, a step the practice staff misunderstand. For each, name the
one preventive action to take before release and whether it is done. Then score readiness from 1 to 10 and say
ship or wait, with the reason. A score under 7, or any open action on patient data, tenancy or money, means wait.
Put the pre-mortem in the pull request or release notes.

## 3. Independent review
Ask a reviewer that did not write the change, in a fresh context: a read-only subagent, with a different model
where the host allows it. Give it only:
- the diff (`git diff <base>...HEAD`),
- the acceptance checks from the brief or the design record,
- the repository's rules (the RULES file and any `docs/design/` record for the change).

It returns findings as `path:line`, severity (blocker, major, minor) and the problem, with no praise and no style
notes that do not change meaning. It checks in this order: acceptance checks met; cross-tenant access; patient data
in logs, errors, fixtures or prompts; money or amounts changed silently; missing tests for a changed behaviour;
secrets; then everything else.

## 4. Fix and repeat
Fix every blocker and major finding, commit, and review the new commit. Stop after three rounds and report what is
still open rather than looping.

## 5. Record the result against the commit
```
nodaris-harness review record --verdict pass --reviewer "<who reviewed>" --findings <count still open>
nodaris-harness review status
```
Record `--verdict fail` when blockers remain; the person then sees that in the approval request.

## 6. Ship through the gate
- Push or open the pull request. The harness stops the call and shows the person the approval command and the
  review state. Wait for the approval; repeat exactly the same call afterwards.
- Never push to a protected branch, never bypass a hook, never add an authorship marker to a commit or pull request.
- After it lands, confirm the remote has the commit you reviewed (`git ls-remote`), and say so with the commit id.
