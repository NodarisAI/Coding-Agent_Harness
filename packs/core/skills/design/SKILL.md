---
name: design
description: Use before implementing a feature, fix or new app whose shape is not obvious, and always when the work touches patient data, money, tenancy or authentication (the harness requires a design record for those). Grounds the design in the real code, produces at least two structurally different options from independent designers, judges them against a rubric, and writes one design record the implementation then follows.
---

# Design: two real options, one judged choice, one record

When the router marks a feature or new app as touching patient data, money, tenancy or authentication, the harness
checks at the end of the turn for a design record written during the session in `docs/design/`. Check yours with
`nodaris-harness design check docs/design/<file>.md`.

Adapted from the `architect` and `arena` skills in pstack (MIT), rebuilt for the Nodaris harness: the rubric always
carries the healthcare criteria, sensitive code never leaves the host's own models, and the record is a file the
harness can check.

## 1. Ground
Run the investigate skill over every system the change touches. Name the existing patterns the change must follow
and every reader and writer of any data whose meaning changes. Skip this only for a new app with no surrounding code.

## 2. Frame the rubric
Write four to six criteria that can be graded. Always include:
- **Isolation:** one practice can never read or change another practice's data, and the design shows where that is
  enforced.
- **Patient data:** minimum necessary, never in logs or model prompts in plain form, encrypted where stored.
- **Failure:** bad input is refused with a typed error, amounts are never silently repaired, retries cannot double
  a side effect.
- **Fit:** uses the repository's existing patterns and frameworks; adds no second way to do an existing job.
- **Testability:** every acceptance check maps to a test.
Add the criteria specific to the task.

## 3. Design it twice
Ask two or three independent designers for the design, in one message so they run in parallel. Each gets the same
task and grounding, and a different starting angle (for example simplest-that-works, risk-first, and
data-model-first). Where the host lets you choose, use different models for different designers.
Each returns a design package of under 600 words:
- the caller's usage written first (how code will call it),
- the types, signatures and module boundaries, with bodies left unimplemented,
- the data flow for the main path and one failure path,
- the alternatives it considered and rejected.

Code or data that is sensitive stays with the host's own models; never send it to an outside model service.

## 4. Judge
- Screen each option for red flags: a module that only passes calls through, a detail that leaks across a boundary,
  a design shaped by the order of steps rather than by the data, and a type that needs casts or always-set optional
  fields.
- Score each option against the rubric, criterion by criterion. If you can, ask one read-only judge with a different
  model to score them too; where you and the judge disagree, reread both before deciding.
- Pick the base that a later maintainer can extend without breaking its rules. When two are close, take the smaller
  public surface.
- Graft at most one or two ideas from the other options, by hand, so the result still reads as one design.
- If the options diverge wildly, the task was under-specified: reframe and run again rather than averaging them.

## 5. Write the record
`docs/design/<YYYY-MM-DD>-<topic>.md` with these sections, in this order:
- `## Problem`: one paragraph.
- `## Options`: each option under its own `### ` heading, with its usage sketch and data flow.
- `## Chosen`: the base and the grafts, with the rubric scores.
- `## Rejected`: each rejected option and the reason.
- `## Acceptance checks`: the observable checks, each mapped to the test that proves it.
- `## Review` (added in step 6): each reviewer finding and its answer.

## 6. Challenge it before any code
For work that touches patient data, money, tenancy or authentication, the record needs an approved adversarial
review of its current content before the harness lets the work finish.
- Ask a reviewer that did not write it, in a fresh context (a read-only subagent on the strongest model the host
  offers, such as Opus): "Find how this design fails. Return findings as JSON: severity (high, medium, low), the
  problem, the section." Give it only the record, the grounding and the rubric.
- Answer every finding in a `## Review` section of the record: the change made, or the reason it is rejected. The
  reviewer rules on each answer. Stop after three rounds and bring what is still disputed to the person.
- Record the verdict against the record's exact content. Approval is refused while a high or medium finding is open,
  and any later edit to the record makes the approval stale:
  `nodaris-harness design review docs/design/<file>.md --verdict APPROVED --findings findings.json --reviewer "<who>"`

Adapted from the plan-review loop in chaseai-yt/claudex-loop and the debate step in NulightJens/rocket-fuel-skill
(both MIT).

## 7. Implement against the record
The record is the contract. Write the tests for the acceptance checks first. A change the record did not
anticipate is a signal: decide whether the record was wrong, a requirement was missed or the code is overreaching,
and update the record before continuing. If the same kind of workaround keeps appearing, the design is wrong: scrap
it, investigate what was built, and design again from step 3 with the new constraints as starting assumptions.
