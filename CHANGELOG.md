# Changelog

## Unreleased

- `install.py` is built and works end to end: onboarding questions, `--dry-run`, `--host`, `--packs`, `--answers`, `--uninstall`, `--reconfigure` and `--no-motion`. `AGENTS.md` and the docs no longer describe it as being built.
- Added a live panel, `nodaris-harness watch` (`--split` for a side pane in tmux), that shows tokens burning with an animation, where they went, subagents, memories pulled and files touched.
- Added `nodaris-harness statusline`, a one-line version of the same numbers for Claude Code's status line. The installer wires it in only when the person has no status line of their own, and removes it at uninstall.
- Subagent launches now get an automatic capsule (route, design record, design-system files, repository rules, reporting contract; paths only, never file contents) appended to the prompt, and a checkpoint after each subagent returns with its token cost and what to review.
- Added a per-session subagent token budget by plan (pro 150k, max 600k, team 400k, api 300k tokens); a launch past the budget is refused with instructions, and `nodaris-harness budget --add N` raises it. Inside a subagent, a reminder is added every 25 tool calls and it is told to stop at 90.
- Added the branch rule: the first edit on `main`, `master`, `prod`, `production`, `staging` or `release` is sent back once with a `git switch -c feat/<topic>` instruction; asking again overrides it. Controlled by the `branch_rule` setting.
- Added reversible delete: `nodaris-harness trash <paths>`, `--list`, `--restore <id>` and `--empty DAYS`. With `reversible_delete` on (the default for new installs), the destructive-command guard refuses a recursive delete outside scratch and build-cache folders and points to the trash command; `# guard:ok` at the end of the command keeps a permanent delete the person asked for.
- The secret guard now also refuses a literal credential — GitHub, GitLab, Slack, Stripe secret, Google, AWS, Anthropic, OpenAI or OpenRouter keys, and private-key blocks — written into a file or a command. The value is never echoed; placeholders such as `ghp_EXAMPLE` pass.
- Team sync (`nodaris-harness sync`, `--dry-run` to preview) pushes only to the person's own `team/<handle>` branch of the team data repository. It never creates that repository; a maintainer creates it first.
- Requests about video, motion, animation, splash screens, ffmpeg, captions, scroll motion or 3D now route automatically to the `creative-studio` skill, so the agent follows the creative pack's method instead of improvising.
- Confirmed the engine runs on Python 3.9 or later.
