# Nodaris Harness: product requirements (draft 1, for review with Varun)

Status: draft, 2026-09-26. Decisions: `ai-os/decisions/2026-09-26-harness-v3-direction.md`.
Research: `second-brain/raw/2026-09-25-harness-research/` and `second-brain/raw/2026-09-26-harness-v3-research/`.
Engineering spec derived from this PRD: `SPEC-v3.md`.

## 1. Problem
General coding agents build healthcare and RCM software the way they build any software: patient data ends up in
logs and prompts, tenant isolation is skipped, fixtures don't match real payer files, work stops before anything is
proven, and consequential actions happen without a person deciding. A vibe coder can't see these mistakes, and
nothing records them, so they repeat.

## 2. Users
- **Vibe coders** (Bharat, practice staff, founders): describe the goal in plain words, get production-grade work.
- **Engineers** (the Nodaris team): the same guarantees with less ceremony on ordinary work.
- **Nodaris**: every session becomes redacted, graded knowledge that improves the harness and, later, our own model.

## 3. Vision (the end of the roadmap)
A Nodaris-owned model for RCM and healthcare agent-building, trained on what the harness records, the team's
lessons and the Knowledge Engine, that makes fewer of a general model's mistakes and doesn't depend on Claude.
Every phase below produces something useful on its own and also produces training data for that model.

## 4. Principles
1. The harness sits inside the agent the person already uses; it is not a new agent.
2. A request is carried out end to end. The person never has to know which skill or command comes next.
3. Rules are enforced by code where the host allows it, and labelled advisory where it doesn't.
4. Consequential actions need a person; ordinary work is never refused.
5. Everything we adopt from outside is rebuilt as our own, with licence credit.
6. Every claim of "done" is backed by recorded evidence.

## 5. Capabilities
Status: **built** = implemented and tested this week; **partial**; **gap** = not built.

| # | Capability | What the user experiences | Status |
|---|---|---|---|
| C1 | One engine, five agents | Same behaviour in Claude Code, Codex, Gemini CLI, Cursor, OpenCode; git hooks for others | built (Claude live-verified; others contract-tested) |
| C2 | Rules of engagement | Routine work runs; pushes, deploys, sends need a one-time approval; bypasses never run | built (policy v0.2 draft, unsigned) |
| C3 | Redaction with stand-ins | Patient identifiers replaced by realistic fakes of the same format; prompts with PHI handed back cleaned | built |
| C4 | Request router | One entry point picks the playbook: bug, feature, refactor, plan, investigate, ship, security | built: `router.py`, 10 playbooks and 6 overlays, route kept per session (live-verified routing) |
| C5 | Investigation | "How does X work" and "why did Y break" answered with evidence before edits | built: `investigate` skill; the Stop gate sends back an answer without evidence once (tested; live run pending quota) |
| C6 | Multi-model design | Several models propose, a judge picks, before architectural work | built: `design` skill (two or more independent designers, rubric with healthcare criteria, judge); the Stop gate requires a design record for sensitive features (tested; live run pending quota) |
| C7 | Review and shipping gate | An independent reviewer bound to the exact commit; merges never bypass protection | built: `ship-review` skill and `review record/status`, bound to commit and tree; push approvals show the review state (tested) |
| C8 | Healthcare build pipeline | Spec first, blueprint for new apps, self-attack tests, PHI lint, done gate, trust receipt | built (v2) |
| C9 | Security assessment | "Check my app's security" runs the full assessment: static checks always; live checks on dev and staging within a scope file the owner signs once per environment; findings with fixes and tests | built: `security check/scope`, signed scope per environment, ZAP live tier, redacted findings log (static tier live-verified in 14 s; live tier tested with a stub) |
| C10 | Team memory | Lessons recalled by prompt and file, shared through the repo | built (basic recall) |
| C11 | Compaction that keeps the asks | Requests and failures survive context compaction | built |
| C12 | Recording and datasets | Every session recorded, redacted and graded; exported for training and evaluation | built (v1 schema) |
| C13 | Benchmark environment | Hidden-rubric tasks, blind judge, harness vs plain | built (rounds 1–6) |
| C14 | Install, doctor, uninstall | One command per agent, diff first, clean undo, self-test | built |
| C15 | Cost and risk before work | An estimate of cost and a confidence signal before a big task starts | gap |
| C16 | Speed | Harness overhead small enough that people keep it on | gap (harness runs ~3x slower than plain in round 6) |

## 6. Roadmap, worked back from the vision
- **Phase 1, trustworthy harness (now):** close C4, C5, C7, C9 and C16 by rebuilding the pstack parts and the
  security workflow as ours; policy signed by Varun; round 7 shows a clear quality lead at acceptable speed.
- **Phase 2, team rollout:** Bharat and the team use it daily; team memory grows; recorded sessions accumulate
  with consent and redaction verdicts.
- **Phase 3, data engine:** graded episodes, synthetic tasks, Knowledge Engine tiers and customer-derived
  knowledge (where contracts allow) assembled into versioned datasets with provenance and licence per record.
- **Phase 4, evaluation suite:** the benchmark becomes a standing RCM/healthcare agent-building eval that any
  model (Claude, open models, ours) is scored on.
- **Phase 5, first Nodaris model:** a small model fine-tuned on Phase 3 data for specific jobs (routing, review,
  PHI detection, spec writing), measured on Phase 4; then larger scope as data grows.

## 7. Answers (2026-09-26 15:12)
Router first, then security as one of its playbooks. Speed judged per benchmark round. No customer-derived data until a
contract is confirmed per customer. Varun is the only signer for now. Roadmap: working harness, then possibly our own
CLI, then the model.
