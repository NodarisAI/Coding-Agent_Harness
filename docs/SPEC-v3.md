# Harness v3: one engine, every coding agent, evidence you can train on

Owner: Varun. Decision to build: Varun in chat, 2026-09-26 14:05 EDT ("once this round six is over ... we have to add
all the features that we discussed ... fully developed for any coding agent that can essentially be used to train
other AI agents"). Builds on SPEC-v2.md (the pipeline) and implements the research that v2 left unused:
`second-brain/raw/2026-09-25-harness-research/blueprint-final.md` (BP) and `rules-of-engagement-v0.2.md` (RoE),
with the cross-agent matrix (note 15), HIPAA controls (13), rule enforcement (10) and scale frameworks (25, 26).

## What Varun asked for, mapped (every request since 2026-09-24, verbatim in the session transcript)
| Ask | v3 answer |
|---|---|
| Works with Claude Code, Codex or any agent, installed into the agent's own config (09-25 18:55, 19:28) | Engine + host adapters (Claude Code, Codex, Gemini CLI, Cursor, OpenCode) + a git-hook layer for agents without hooks (Aider and others); `install` edits the host's own files with a diff, backup and uninstall |
| Rules of engagement researched to the highest standard, not reinterpretable, human approval for each consequential act (18:55, 18:58) | RoE v0.2 compiled into `rules/policy.json` (versioned, hashed): closed-world classes routine / consequential / prohibited decided by code; approvals bound to the exact action hash, given by a person at a terminal, signed with a key the agent is denied |
| Built-in redaction so PHI can be used without a BAA (18:56), Varun's decision 2: synthetic surrogates, never tags, mapping never written | Redaction v3 with same-format random surrogates; prompt and document redaction points; a report with a verdict on every redaction |
| Memory so mistakes are not repeated, team memory harvested later (09-25 05:09, 05:40, 09-26 02:07) | Portable lesson library per repo and per person, recall on prompt, capture on stop, export for central merge |
| Smart compaction that keeps the asks (09-25 06:55) | Portable snapshot and restore of the verbatim asks, open failures and changed files across compaction |
| Delegation and other models' opinions (07:41) | Stays per engineer (OmniRoute is single-user); the policy classes paid lanes as consequential and PHI-bearing content as never delegated |
| Download scanning without prompts (19:08) | Portable download scan on clone and install (secrets, known-vulnerable dependencies, prompt-injection text in agent instruction files) |
| Frameworks for front end, back end, architecture, security, production, scale, pen-test readiness (02:07) | Skills: frontend-quality, backend-architecture, production-readiness (15 checks), pentest-readiness, plus the v2 pipeline skills |
| Prove it works end to end, on the CLI and the app (09-25 23:44, 09-26 00:01) | `doctor` per host: installs, then proves a secret read, a destructive command and a hook bypass are refused and a routine command is not; the benchmark on the final build |
| Never refuse the user's ordinary commands (09-26 00:32) | Only prohibited actions are refused; consequential ones ask for a person's approval; everything else runs |
| Competitive with frontier harnesses; attract Anthropic (15:33, 16:06) | Measured against plain Claude Code on the vibe set; the comparison table on the page; nothing claimed that did not run |
| Usable to train other AI agents (18:05) | Trajectory recorder: every session becomes a redacted, structured episode (request, steps, gate interventions, checks, graded outcome, receipt), exportable as SFT and evaluation datasets; the benchmark ships as a reusable environment of tasks, hidden rubrics and a grader |
| Looks basic; does it work (18:05) | A product layout (one CLI, README, docs, tests, CI, versioned releases) and the redesigned page built on measured results |

## Architecture
```
harness/
  engine/nodaris_harness/     stdlib-only Python package, the only code that runs in hooks
    events.py                 canonical event (Claude-shaped) and host adapters: parse host payload, render host output
    dispatch.py               one process per hook event: guards, policy, redaction, pipeline modules, recorder
    policy.py                 rules of engagement: classify, action hash, approvals (verify, mark used)
    redact.py                 surrogate redaction with a verdict report (vendored phi_guard for detection)
    pipeline/                 intake, tracker, phi_lint, done_gate, repocard (v2 modules)
    memory.py, compact.py     lessons and compaction snapshot
    trajectory.py             episode recorder and exporters
    cli.py                    `nodaris-harness` commands
  hosts/<host>/               per-host templates: rules file, hook wiring, deny rules, skill location
  rules/RULES.md, policy.json the agent-facing rules and the machine policy (hash in the release manifest)
  skills/, agents/            portable SKILL.md skills and agent definitions
  bench/                      tasks, rubrics, runner, grader (the evaluation environment)
  tests/                      unit tests per module, host-contract tests, install tests
```
CLI: `install --host H [--dry-run]`, `uninstall --host H`, `doctor --host H [--live]`, `hook --host H --event E`,
`approve` (terminal only), `redact`, `scan`, `receipt`, `lessons`, `export --format sft|eval`, `bench`.

## Enforcement per host (what is enforced versus advisory, stated honestly)
| Host | Blocking hooks | Rules file | Skills | Verified by `doctor --live` |
|---|---|---|---|---|
| Claude Code CLI and app | PreToolUse, UserPromptSubmit, Stop, PreCompact | CLAUDE.md | skills/ | required |
| Codex CLI | PreToolUse, UserPromptSubmit, Stop, PreCompact (hooks.json) | AGENTS.md | skills/ | required where the account allows |
| Gemini CLI | BeforeTool, AfterTool, BeforeModel | GEMINI.md | extension | contract test; live if signed in |
| Cursor | beforeShellExecution, beforeReadFile, afterFileEdit, stop | .cursor/rules | skills | contract test |
| OpenCode | plugin `tool.execute.before` calling the engine | AGENTS.md | skills | contract test; live if a model is configured |
| Any other agent (Aider, Cline) | git pre-commit and pre-push hooks run scan and PHI lint | AGENTS.md | advisory | contract test |
A host that is not live-verified is labelled so in `doctor` output and in the docs.

## Definition of done (v3)
1. Engine tests pass: `python3 -m pytest -q` in `harness/` exits 0, with contract tests that feed each host's real payload shape through `dispatch` and check the host-specific deny, allow, context and stop outputs.
2. `install`/`uninstall` round-trip on each host into a temporary HOME leaves the files byte-identical after uninstall; `--dry-run` writes nothing.
3. `doctor --live` passes on Claude Code; every other host passes its contract test and is live-tested where an account is available, with the result stated per host.
4. Policy: prohibited, consequential and routine examples from RoE section 3 are classified by code with tests; an approval made at a terminal permits exactly one matching call; a changed argument is denied.
5. Redaction: surrogates keep kind and format, are not derived from the value, one value maps to one surrogate within a run, nothing is written to disk; a document that cannot be scanned is refused.
6. Trajectories: a headless session produces an episode file with no PHI (redaction verdict recorded), and `export` emits SFT and eval datasets that load as JSON Lines with a documented schema.
7. The v2 pipeline is unchanged in behaviour or better; the vibe round on the final build shows a lead of at least 15% of total points and most would-merge verdicts (at most three loops), and the expert regression stays within noise.
8. The team build passes the export check (secrets, PHI, personal material) with every remaining hit reviewed and listed.
9. The page is rebuilt on the measured results; the decision note and memory are updated.

## Non-goals and limits
- No push, repository creation or GitHub change without Varun's approval in chat.
- The RoE stays a draft until Varun signs it; v3 ships its defaults and marks the sign-off as open.
- An approval key readable by the same OS user is not tamper-proof; the managed or admin settings step on each machine (BP section 5) is what locks it, and it needs a person.
- Hosts whose hooks cannot be tested live are shipped as contract-tested only and labelled so.
