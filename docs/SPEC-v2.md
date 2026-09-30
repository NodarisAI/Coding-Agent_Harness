# Harness v2: from rules a model may read to a pipeline it must go through

Owner: Varun. Audience widened 2026-09-26 12:06 to practices and doctors building their own RCM and
healthcare applications, where the harness must supply the trust. Decision to build: Varun in chat, 2026-09-26 11:33 EDT ("go all out ... develop a spec ...
understand what the definition of done is before coming back").

## The problem, measured

Four blind rounds (18 task pairs) put the harness level with plain Claude Code: 229 against 232 of 270
through round 3, and a harness lead only in round 4 on Fable 5. The cause is the benchmark, not only the pack:
every task prompt is a senior engineer's spec. It names the file, the bug, the pattern to copy, the threat
model to write and the test command. That is the work the harness exists to do for a teammate who cannot
write that spec. Both arms were handed the expert's brain, so the harness had nothing left to add but rules.

The second cause is the pack itself. v1 is a rules file, nine linked skills and Varun's personal hooks. The
rules are advice. Nothing makes the model investigate a vague ask, attack its own change, or prove it ran
the tests before it says "done". Plain Claude Code with a good prompt does the same things by itself.

## Who it is for

Bharat and the rest of the team: people who describe what they want in a sentence ("patients from other
clinics can see each other's stuff, fix it"), do not know the repo's verify command, do not know what a
cross-tenant test is, and will accept "done" at face value. The harness must turn that sentence into the
work a senior healthcare engineer would do, without them asking for it.

## What v2 adds (all pack-owned, under `harness/pack/`, no edits to Varun's installed guards)

1. **Intake hook** (`hooks/intake.py`, UserPromptSubmit). No model call. Reads the prompt and the repository
   and injects a work order:
   - A repo card: the stack, the verify and test commands it detected (pytest, npm scripts, Makefile, tox,
     manage.py), and where tests live. A vibe coder never has to know these.
   - A classification: is the ask vague (no file, symbol or command named), and does it touch a sensitive
     surface (patients, PHI, tenants, auth, tokens, payments, claims and remittances, uploads, webhooks,
     model features)? Vague asks get the spec-first procedure; sensitive asks get the security procedure.
2. **Spec-first skill** (`skills/spec-first`). Turns a sentence into an engineer's brief before any edit:
   find the code that owns the behaviour, its callers and its tests; state the defect or feature
   precisely; list observable acceptance checks; pick the repo's existing framework patterns (DRF
   permissions and serializers, Django migrations, zod or typed API clients) instead of inventing new ones;
   decide without asking when nobody can answer, and record the decision.
3. **Self-attack skill** (`skills/self-attack`). After building on a sensitive surface, the model attacks
   its own change: cross-tenant and object-level access (BOLA/IDOR), missing or bypassable
   authentication, mass assignment, injection (SQL, X12 delimiters, CSV formulas, prompt injection into
   model features), PHI leakage through logs, errors, responses and audit rows, oversized and malformed
   input, replay and duplicate submission. Each applicable attack becomes a test that fails on the unsafe
   version, and the report table in the summary says which attacks were tried and how each is blocked.
4. **Healthcare domain skill** (`skills/healthcare-domain`). The knowledge a vibe coder lacks: the HIPAA
   technical safeguards as code requirements, the 18 Safe Harbor identifiers, minimum necessary, audit
   records that carry counts and kinds, X12 traps (CAS pairs, delimiter injection, envelope parsing),
   synthetic fixture rules, and the Nodaris failure catalogue.
5. **PHI lint hook** (`hooks/phi_lint.py`, PostToolUse on writes). Deterministic scan of what was just
   written: logging or printing of patient fields, exception text interpolated into logs, realistic
   identifiers in fixtures (SSN, phone, MRN and member-id shapes, real-looking name plus date of birth). It
   does not block; it tells the model exactly which line to fix, so redaction happens at write time.
6. **Done gate** (`hooks/done_gate.py`, Stop, blocking and bounded). When the session changed code, the
   model may not finish until: a test or verify command ran after the last code change and exited 0; and,
   if a sensitive file changed, a test file with adversarial cases was written and run. It blocks at most
   twice per session, names the exact command to run, and never blocks a turn that changed no code. This
   replaces "never claim done without evidence" as advice with a gate.

7. **Trust receipt** (`tools/trust_report.py`). Varun, 2026-09-26 12:06: practices and doctors who build their own
   applications need a reason to trust what was built. For any session, the receipt lists from recorded evidence,
   never from the model's own summary: the checks that ran and their results, the sensitive files changed, the
   adversarial tests written and run, the PHI lint findings raised, the times the done gate stopped the model and
   why, and what was never verified. A reviewer who cannot read code can see what was proven.


## The goal, mapped in full (added 2026-09-26 after Varun: "understand the goal in every aspect")

The harness is intellectual property only if, for the people it serves, it does something a frontier harness
(Claude Code as shipped, Codex CLI, Cursor, Kiro, Devin, OpenHands, Aider) does not: it makes software built by
people who are not healthcare security engineers trustworthy enough for production in revenue-cycle and clinical
settings, and it proves that with evidence a non-engineer can read.

### Who uses it
| User | What they type | What they cannot do themselves | What the harness must supply |
|---|---|---|---|
| U1 senior engineer (Varun) | a precise spec | nothing essential; wants speed | stay out of the way; guards and memory only |
| U2 vibe-coding teammate (Bharat) | a symptom or a wish | find the code, know the verify command, think like an attacker | intake, spec-first, self-attack, done gate |
| U3 practice or doctor building their own app | "I want an app that..." | choose a stack, design tenancy, auth and audit, judge "done" | a healthcare app blueprint, the same pipeline, a trust receipt |
| U4 unattended agent (CI, Agent Teams) | a task file | ask anyone | safe defaults, recorded decisions, gates that cannot be talked past |

### What kind of work
W1 fix or feature in an existing repo; W2 a new application from nothing; W3 a security fix; W4 an integration
(X12 837/835/270/271, a clearinghouse, an EHR API, file drops); W5 a model or agent feature over PHI; W6 data and
migrations; W7 user interface; W8 getting ready to deploy.

### The stages every piece of work passes through, and who covers each
| Stage | Plain Claude Code | Frontier best practice | Harness v2 |
|---|---|---|---|
| Intake | prompt as typed | Kiro turns a prompt into requirements | intake hook: repo card, vague and sensitive classification |
| Spec | none unless asked; plan mode | Kiro requirements/design/tasks with EARS criteria | spec-first skill; spec files for architectural work |
| Context | CLAUDE.md, grep | Aider repo map, OpenHands path triggers | repo card, path trigger on sensitive files, memory recall |
| Build | model's own habits | Aider lint-on-edit, LSP diagnostics | PHI lint at write time; test-first rule |
| Verify | model decides | Codex runs tests; Devin checks | done gate: a passing check after the last change |
| Secure | nothing | Codex sandbox; scanners in CI | self-attack tests, diff security scan, guards, deny rules |
| Review | none | Codex review, Amp oracle | independent read-only security reviewer agent |
| Evidence | model's summary | PR description | trust receipt built from recorded events |
| Learn | auto memory | none of note | memory loop (Varun's machine), lessons |
| Ship | nothing | CI templates | blueprint's CI and verify script; push approval |

### The risks that matter in this domain, and what stops each
| Risk | Stopped by |
|---|---|
| PHI in logs, errors, fixtures, prompts | PHI lint (write time), redaction tool, PHI gate, healthcare-domain skill |
| One tenant reading another's data | intake and path trigger briefing, self-attack cross-tenant tests, done gate |
| Broken or bypassable auth | self-attack, blueprint auth design |
| Silently corrupted money or claims | healthcare-domain X12 rules, typed-error rule, self-attack money cases |
| Secrets leaked or committed | secret guard, deny rules, diff scan (gitleaks) |
| Vulnerable code patterns and dependencies | diff scan (semgrep, bandit, osv-scanner) |
| Destructive commands | destructive guard, deny rules |
| Prompt injection in model features | self-attack prompt-injection cases, secure-build rules |
| Claiming work that was not done | done gate, trust receipt |
| The harness refusing the user | "the user decides" rule |

### Gaps this map exposed, closed in v2.1
1. **New applications (W2, U3):** nothing helped someone start from zero. Added the `healthcare-app-blueprint`
   skill: an opinionated reference architecture (tenancy, auth with MFA, audit log, field encryption, PHI-safe
   logging, verify script, CI, threat model, synthetic seed data) and its acceptance checks.
2. **Security scanning in the loop:** scanners ran only in a separate baseline. Added `tools/scan.py` (changed
   files only, seconds) and the done gate now requires a clean scan after a sensitive change.
3. **Sensitive work the prompt did not announce:** added the path trigger on the first sensitive edit.
4. **Independent review:** added a read-only `security-reviewer` agent that reviews a sensitive diff with the
   self-attack checklist; its run is recorded in the trust receipt.
5. **Spec files for big work:** spec-first now writes `docs/specs/<feature>.md` (requirements with EARS
   acceptance criteria, design, tasks) for architectural requests.
6. **Using it at all:** the pack depended on Varun's own `~/.claude`. Added a self-contained team build that
   vendors the guards, skills and tools, leaves out personal material, and passes the export check.

### Built in v2.1 (2026-09-26), and what round 5 changed
- Gaps 1 to 6 are built: `skills/healthcare-app-blueprint`, `tools/scan.py` (also scans the last commit when
  nothing is uncommitted, so committing does not escape it), the path trigger, the `security-reviewer` agent,
  EARS spec files in `spec-first`, and the team build (`team.py`: `vendor` on the owner's machine, `build DEST`
  anywhere; guards vendored with exact substitutions and checked decision-for-decision against the installed
  originals on 17 probes each; `harness-export-check.py <folder>` scans the built folder).
- Round 5 (v2.0, stopped after one task): harness 9, plain 7 on the security task, but the harness arm opened no
  skill, so it wrote no threat model, left a request-supplied viewer id unbound and did not audit refusals. Skills
  the model may skip are advice. Fix: the eight-point security checklist now travels in the hook context itself
  (intake and path trigger), and the done gate requires a threat model note for any sensitive change.
- The benchmark's harness arm is now the team build (`dist/team`), the configuration a teammate or a practice
  actually installs, not the owner's personal configuration with its memory of these repositories.
- Smoke run of the team build on a toy tenant bug, headless, Sonnet 5: intake briefing, one done-gate stop for
  missing attack tests, attack tests, clean scan, threat model note, security-reviewer run (found two more
  fail-open paths, fixed), trust receipt with every line proven.

### Honest limits against frontier harnesses
- Speed: the harness does more, so it takes longer. Accepted by Varun.
- Model choice: Claude only. Codex, Cursor and Devin are not measured here; plain Claude Code is the frontier
  baseline, run with the same model on the same tasks.
- OS sandboxing is Claude Code's own `/sandbox`, off by default. The team install notes recommend it; benchmarks run without it so both arms match.

## The benchmark that measures the right thing

- **Vibe set** (`bench/tasks/v-*`): the same eight tasks, same repositories and base commits, but the prompt
  is what a vibe coder would type: a symptom or a wish in one to three sentences, no file names, no
  commands, no named patterns. The hidden rubric stays expert-level. Rubric lines that name an exact output
  path are loosened to the substance ("a threat model document in docs/"), since neither arm was told the
  path.
- **Trap probes**: two vibe tasks that tempt a bad practice a teammate would plausibly ask for: logging the
  full incoming claim to debug a failure, and seeding "realistic" demo patients. Scored by the same rubric
  method.
- Arms run Sonnet 5 on the subscription (what a teammate uses), judged blind by Opus 5. Claude never runs
  on OpenRouter.
- The expert set stays as the regression check: v2 must not lose ground there beyond judge noise.

## Definition of done

1. The seven components exist, each hook and tool has unit tests, and `python3 -m pytest -q pack/hooks/tests pack/tools/tests tests` (from `harness/`) exits 0.
2. `harness/build-config.py` builds a config that includes the pack hooks and skills and still carries
   every installed safety hook and deny rule (the build refuses otherwise), and a headless smoke run shows
   the intake context and the done gate firing.
3. The vibe set, the trap probes and one new-application task exist with rubrics, and a full vibe round has run on both arms.
4. On the vibe round the harness leads plain by at least 15% of total points, and the judge would merge
   the harness's work on most tasks. If not, the losses become fixes and the round repeats (at most three
   loops before reporting honestly where it stands).
5. The expert-set regression (the three hard tasks) is within judge noise of v1 or better.
6. The anatomy page shows v2: the pipeline, each new part, what it catches, and the vibe-round evidence.
7. A decision note records rounds 2 to 6 and the v2 results; the harness-benchmarks memory is updated.
8. The team build builds from the harness folder alone, its tests pass, the export check is clean on the built
   folder, and a headless smoke run on it shows every stage and a complete trust receipt.

## Non-goals and limits

- No change to Varun's installed guards (`~/.claude/hooks`); guard changes go through hooks-staging and his
  install script.
- No push, PR or GitHub change. Everything stays local until Varun approves.
- Speed is not a goal. Tokens are reported, not optimised, in this pass.
- No model calls inside the new hooks: they must work for a teammate with no API keys and cannot leak PHI.
