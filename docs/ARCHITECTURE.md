# Architecture

One engine, many coding agents. Each agent (the host) calls the engine from its own hook system; the engine decides, records and adds context, then answers in that host's format.

```
  host agent (Claude Code, Codex, Gemini CLI, Cursor, OpenCode)      git (any other agent)
        │ hook event (JSON on stdin)                                    │ pre-commit / pre-push
        ▼                                                               ▼
  bin/nodaris-harness hook --host H ──► events.py: parse host payload   nodaris-harness gitcheck
        │                                                               (scan, protected branches,
        ▼                                                                AI-authorship marks)
  dispatch.py, one process per event
    1. guards: secrets, destructive commands          (errors refuse the call)
    2. policy.py: routine / consequential / prohibited (errors refuse the call)
    3. redact.py: prompts and outgoing text get stand-ins
    4. router.py: playbook + overlays for the session
    5. pipeline: intake, tracker, PHI lint, copy and design lint, memory recall
    6. gates.py + done gate at Stop
    7. trajectory.py: redacted step appended to the episode; signals.py: learner inputs
        │
        ▼
  events.py: render the decision in the host's format (deny, allow, extra context, block stop)
```

## Engine

`engine/nodaris_harness/` is a standard-library-only Python package (Python 3.9 or later), so it runs on any machine without installing anything. `bin/nodaris-harness` is its entry point; `cli.py` holds every command. A pipeline step that fails on its own bug is recorded and the call proceeds; only the guards and the policy fail closed.

## Hooks per host

| Host | Events wired | Rules file | Level |
|---|---|---|---|
| Claude Code | PreToolUse, UserPromptSubmit, PostToolUse, PostToolUseFailure, Stop, PreCompact, SessionStart (`settings.json`) | `CLAUDE.md` | live-verified with `doctor --live` |
| Codex CLI | PreToolUse, UserPromptSubmit, PostToolUse, Stop, PreCompact, SessionStart (`hooks.json`) | `AGENTS.md` | contract-tested |
| Gemini CLI | BeforeTool, BeforeAgent, AfterTool, AfterAgent, PreCompress, SessionStart (`settings.json`) | `GEMINI.md` | contract-tested |
| Cursor | beforeShellExecution, beforeReadFile, afterFileEdit, afterShellExecution, postToolUseFailure, beforeSubmitPrompt, stop, preCompact, sessionStart | rules | contract-tested from published docs |
| OpenCode | plugin `tool.execute.before` | `AGENTS.md` | contract-tested; no prompt, stop or session hooks, so intake, prompt redaction and the done gate are advisory |
| Any other | git pre-commit and pre-push | `AGENTS.md` | advisory for the agent |

Exact payload shapes are in `docs/HOST-CONTRACTS.md`. `install` writes a diff, a backup and a manifest (`~/.nodaris-harness/installs/`); `uninstall` restores from the backup.

## Policy classes

`rules/policy.json` (versioned, hashed; currently an unsigned draft) is compiled from the rules of engagement. Rules match the program a shell segment actually runs, not text inside quotes or heredocs.

- **Routine:** anything not listed. Runs and is recorded.
- **Consequential** (`C-PUSH`, `C-PUBLISH`, `C-DEPLOY`, `C-REMOTE`, `C-SEND`, `C-DB-DESTRUCTIVE`, `C-MACHINE-CHANGE`, `C-PAID-MODEL`, `C-PHI-SOURCE`): refused with a hash until a person runs `nodaris-harness approve HASH` in their own terminal. The approval covers one call with exactly those arguments.
- **Prohibited** (`P-HOOK-BYPASS`, `P-UNPARSEABLE`, `P-AGENT-LAUNCH`, `P-APPROVAL-STORE`, `P-AI-MARK`, `P-PROTECTED-PUSH`): never run from an agent.

`nodaris-harness policy --explain "CMD"` shows how a command is classed.

The secret guard (`packs/core/vendor/guards/secret-guard.py`) also refuses a literal credential value — GitHub, GitLab, Slack, Stripe secret, Google, AWS, Anthropic, OpenAI and OpenRouter keys, and private-key blocks — written into a file or a command; the value is never echoed to the log. A placeholder such as `ghp_EXAMPLE` passes.

## Router, playbooks and overlays

`router.py` matches each prompt by rules, not a model, to one playbook: investigate, bug-fix, feature, new-app, rcm-data, security-check, plan, refactor, perf, ship or media. Overlays add domain rules on top: autonomous, rcm, phi, tenant-money, auth, hardening and model-feature. The brief ends with a risk level and the gates that will apply. The route is kept for the session and recorded, so routing decisions become labelled examples.

A request about video, motion, animation, splash screens, ffmpeg, captions, scroll motion or 3D routes to `media`, which points the agent at the `creative-studio` skill so it follows the pack's method instead of improvising. The creative pack (nine skills, listed in `packs/creative/README.md`) is separate from the healthcare pack.

## Gates

- **Done gate:** a coding turn cannot end until a check passed after the last code change; after a sensitive change, adversarial tests and a clean `scan` are also required.
- **Evidence gate:** investigations and bug fixes must cite a file and line, a command with its exit code or quoted output.
- **Design gate:** a feature touching patient data, money, tenancy or authentication needs a hash-bound design record in `docs/design/` (`nodaris-harness design check|review`).
- **Review gate:** reviews are bound to a commit and its tree (`nodaris-harness review record|status`); push approvals show the review state.
- **Security check:** static checks always; live checks only against hosts in a scope file the owner signs per environment (`nodaris-harness security scope`), dev and staging by default.

Each end-of-turn gate sends the agent back at most once per session, so a gate cannot trap a session.

## Learner and memory

- **Lessons:** recalled on each prompt and when a file they name is edited. Team lessons live in `<repo>/.nodaris-harness/lessons.jsonl`; personal ones in the harness home.
- **Self-learning loop:** `signals.py` records corrections, gate outcomes and failed checks; `learner.py` runs out of band (at most once a day, at session start, skipped when `NODARIS_HARNESS_NO_BG` is set) and applies safe changes (profile, router keywords, lessons seen three or more times) under `rules/learner-policy.json`. Anything touching a gate needs one approval. The learner can never write the policy, guards or engine. Every change is shown at the next session start and can be reverted with `nodaris-harness learn revert ID`.
- **Compaction:** the verbatim asks, open failures and changed files are snapshotted before compaction and restored after.

## Packs

`packs/core` (always): guards, hooks, tools (`scan`, `redact`, trust receipt), the security-reviewer agent and the general skills. `packs/healthcare`: healthcare domain rules and the new-app blueprint. `packs/creative`: motion, WebGL and product-film skills. The installer selects packs by how the person will use the harness.

## Subagent capsule and checkpoints

Every `Task`/`Agent` launch gets the orchestrator's frame appended to its prompt: the session's route, the design record and design-system files the work must follow, the repository rules and the reporting contract. The subagent reads the files itself; the capsule carries paths, never file contents. When a subagent returns, the orchestrator gets a checkpoint with its token cost, the session total against the budget, and what to review before accepting the work.

The per-session subagent token budget is set by plan at onboarding (pro 150k, max 600k, team 400k, api 300k tokens). A launch past the budget is refused with instructions; `nodaris-harness budget --add N` raises it. Inside a running subagent, a reminder is added every 25 tool calls and it is told to stop and hand back what it has at 90.

## Branch rule

The first edit on `main`, `master`, `prod`, `production`, `staging` or `release` is sent back once (`gates.branch_check`, wired in `dispatch.py`) with the `git switch -c feat/<topic>` instruction (or `fix/`, `docs/`, `chore/`). If the person asked to work on that branch directly, asking again lets the edit through. Controlled by the `branch_rule` setting (on by default).

## Reversible delete

`nodaris-harness trash <paths>` moves files into `<harness home>/trash/<id>/` instead of deleting them; `--list` shows entries, `--restore ID` puts one back, `--empty DAYS` is the only permanent step and only removes entries older than that. With the `reversible_delete` setting on (the default for new installs), the destructive-command guard refuses a recursive delete outside scratch and build-cache folders and points to the trash command; `# guard:ok` at the end of the command keeps a permanent delete the person asked for.

## Live panel and status line

`nodaris-harness watch` tails one session transcript read-only, and every subagent transcript under `<session>/subagents/` while the subagent runs. It shows what the agent is doing now, tokens used for the request, the session, the day and the week (new input, cache writes and output; cache re-reads apart, since they cost about a tenth), the context size against the model's window, a ten-minute timeline of tokens with each prompt marked, subagents with their live tokens and current tool, the memories and lessons pulled for the latest request with why they matched, where the tokens went and files touched. `nodaris` starts Claude Code with `NODARIS_PANEL_LINK` set; the SessionStart hook writes that session's transcript path to `<harness home>/live/<link>.json`, so the panel follows exactly that session through /clear, /resume and compaction. Day and week figures come from `usage.py`, a ledger that reads every transcript incrementally by byte offset and counts each API message once. The Stop hook adds one line with the same figures as Claude Code's `systemMessage`, shown in the terminal and the desktop app (`turn_summary: false` turns it off). None of this prints file contents, prompts or command output. `nodaris-harness statusline` is a one-line version of the same numbers for Claude Code's own status line; `install.py` wires it into `settings.json` only when the person has no status line of their own, and removes it at uninstall.

## Data locations

| Path | Holds |
|---|---|
| `~/.nodaris-harness-src` | The clone the hooks run from |
| `~/.nodaris-harness/` (or `$NODARIS_HARNESS_HOME`) | Everything below; never leaves the machine except through opt-in sync |
| `episodes/` | Redacted session recordings, exportable with `nodaris-harness export --format sft\|eval` |
| `keys/`, `pending/`, `approvals/`, `used/` | The approval store (agents are denied access) |
| `installs/`, `backups/` | Install manifests and the backups uninstall restores from |
| `lessons/`, `signals/`, `profile/`, `snapshots/`, `sessions/`, `reviews/`, `design-reviews/` | Personal lessons, learner inputs, your profile, compaction snapshots, gate state and review records |
| `<repo>/.nodaris-harness/` | Team lessons and the signed security scope |
| `<repo>/docs/design/`, `<repo>/docs/security/` | Design records and redacted security findings |
