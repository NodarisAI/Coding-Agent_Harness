# Changelog

## Unreleased

- The live panel now follows exactly the session `nodaris` started. It used to pick the newest transcript in the folder, so another open session there (the desktop app) could show instead.
- Tokens used (new input, cache writes and output) are reported apart from cache re-reads. The old total counted every re-read of the conversation, which is billed at about a tenth, and ran to tens of millions for a long session.
- Subagents show their tokens and current tool while they run, read live from their own transcripts. A subagent whose transcript has ended is shown as finished, and one silent for half an hour as stalled.
- The flame animation is replaced by live information: what the agent is doing now with a timer, a ten-minute token timeline with each prompt marked, numbers that count up with a "+N" after each increase, and a context gauge against the model's window.
- Added `nodaris-harness usage` and day and week figures in the panel, from a ledger that reads every transcript once and counts each API message once, even when a resumed session copies it.
- At the end of each reply, Claude Code (terminal and desktop app) shows one line with the tokens used for the request, the session and the day. `turn_summary: false` in the settings turns it off.
- The panel shows the memories and lessons pulled for your latest request, including the harness's own lessons, which it did not recognise before, and says why each matched.
- Lesson recall now needs at least two shared words, one of them a keyword or part of the situation the lesson describes; words common across many lessons count for less.

- `install.py` is built and works end to end: onboarding questions, `--dry-run`, `--host`, `--packs`, `--answers`, `--uninstall`, `--reconfigure` and `--no-motion`. `AGENTS.md` and the docs no longer describe it as being built.
- Added a live panel, `nodaris-harness watch` (`--split` for a side pane in tmux), that shows tokens burning with an animation, where they went, subagents, memories pulled and files touched.
- Added `nodaris-harness statusline`, a one-line version of the same numbers for Claude Code's status line. The installer wires it in only when the person has no status line of their own, and removes it at uninstall.
- Subagent launches now get an automatic capsule (route, design record, design-system files, repository rules, reporting contract; paths only, never file contents) appended to the prompt, and a checkpoint after each subagent returns with its token cost and what to review.
- Added a per-session subagent token budget by plan (pro 150k, max 600k, team 400k, api 300k tokens); a launch past the budget is refused with instructions, and `nodaris-harness budget --add N` raises it. Inside a subagent, a reminder is added every 25 tool calls and it is told to stop at 90.
- Added the branch rule: the first edit on `main`, `master`, `prod`, `production`, `staging` or `release` is sent back once with a `git switch -c feat/<topic>` instruction; asking again overrides it. Controlled by the `branch_rule` setting.
- Added reversible delete: `nodaris-harness trash <paths>`, `--list`, `--restore <id>` and `--empty DAYS`. With `reversible_delete` on (the default for new installs), the destructive-command guard refuses a recursive delete outside scratch and build-cache folders and points to the trash command; `# guard:ok` at the end of the command keeps a permanent delete the person asked for.
- The secret guard now also refuses a literal credential — GitHub, GitLab, Slack, Stripe secret, Google, AWS, Anthropic, OpenAI or OpenRouter keys, and private-key blocks — written into a file or a command. The value is never echoed; placeholders such as `ghp_EXAMPLE` pass.
- Team sync shares with the Nodaris memory vault: once a day at session start (opt-in), each member's redacted lessons go as pages to `team/<handle>/` on their own `agent/team-memory/<handle>` branch, with a telemetry file of counts, learner changes and subagent tokens. `nodaris-harness team-intake` merges the members' branches into the vault for review, and every sync brings the team's merged lessons back into recall.
- Onboarding is offered only once, in the first new session after install. Later sessions, prompts and tool calls never offer it again; `nodaris-harness onboard` runs it at any time.
- `nodaris-harness ready` answers "are we done?": it runs an acceptance list (`.nodaris/acceptance.json`) item by item, and `--arm` makes the Stop hook send the agent back to work while a check fails, for at most six rounds. Steps only a person can take are reported as waiting on that person.
- The core pack's deny rules (credential files, key stores, `.env` files) are now installed for Claude Code in every install, next to the person's own rules; uninstall removes only the ones it added. The `deny_rules` setting turns them off.
- Requests about video, motion, animation, splash screens, ffmpeg, captions, scroll motion or 3D now route automatically to the `creative-studio` skill, so the agent follows the creative pack's method instead of improvising.
- Confirmed the engine runs on Python 3.9 or later.
