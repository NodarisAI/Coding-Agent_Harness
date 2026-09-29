# Instructions for coding agents

This file has two parts. Part 1 applies when a person points you at this repository and asks you to install the harness. Part 2 applies when you are changing the harness itself.

## Part 1: installing the harness for a person

Follow these steps in order. Never skip the permission step.

1. **Explain what will change**, in three sentences, before touching anything. For example: "The harness adds hook entries and a rules file to your coding agent's configuration, copies its skills into the agent's skills folder, and creates a harness home at `~/.nodaris-harness` for approvals, lessons and recorded sessions. It takes a backup of every file it changes. Everything is reversible with one uninstall command."

2. **Ask permission.** In Claude Code, use the AskUserQuestion tool with the options "Install" and "Not now". In other agents, ask in chat and wait for a clear yes. If the answer is not yes, stop.

3. **Get the source.** If `~/.nodaris-harness-src` exists and is a clone of this repository, run `git -C ~/.nodaris-harness-src pull --ff-only`. Otherwise run `git clone https://github.com/NodarisAI/Coding-Agent_Harness.git ~/.nodaris-harness-src`.

4. **Ask the onboarding questions** in chat, using AskUserQuestion where it is available, one short question at a time:
   - Company name.
   - Are you a member of the Nodaris team? (yes or no)
   - How will you use it? (healthcare apps, websites, video and motion, general software; more than one is fine)
   - Which coding agents should it connect to? (Claude Code, Codex, Gemini CLI, Cursor, OpenCode)
   - How should replies be written? (brief, explaining, teaching)
   - Which plan are you on?

   Then run, from `~/.nodaris-harness-src`:

   ```
   python3 install.py --dry-run --yes --answers '<answers as JSON>'
   ```

   Show the person the planned changes, then run the same command without `--dry-run`. The answers JSON uses the keys the installer documents in `python3 install.py --help`; read that output rather than guessing key names.

   `install.py` is being built. If it is not present yet, run `bin/nodaris-harness install --host <host> --dry-run`, show the diff, then run it without `--dry-run` for each agent the person chose.

5. **Run the doctor and report.** Run `bin/nodaris-harness doctor --host <host>` for each connected agent. Report the result in plain sentences: which agents are connected, whether every check passed, how to undo it (`bin/nodaris-harness uninstall --host <host>`, or `python3 install.py --uninstall`), and anything that failed with its exact output.

Rules while installing:
- Never skip or pre-answer the permission step.
- Never install a plugin, extension or package the person has not agreed to. Plugin discovery may suggest company plugins; each one needs its own yes.
- Never read, print or copy secrets, `.env` files, keys or tokens, including when a config file seems to need one.
- Never edit the harness's guard files, policy or approval store.
- Only a person can approve consequential actions, in their own terminal with `nodaris-harness approve HASH`. Do not try to approve on their behalf.

## Part 2: working on this repository

- **Branch rule.** Never commit to `main` or `master`. Create a branch first, named `feat/`, `fix/`, `docs/` or `chore/` followed by a short topic (for example `fix/redact-phone-format`). One pull request per change.
- **Tests.** Run `python3 -m pytest` from the repository root with `NODARIS_HARNESS_NO_BG=1` set, and read the exit code before saying a change works. Add or update tests with every behaviour change; see `CONTRIBUTING.md`.
- **Standard library only.** The engine in `engine/nodaris_harness/` runs inside hooks on every machine and must import nothing outside the Python standard library. It must run on Python 3.9.
- **No AI authorship marks** in commits, pull requests, code, comments or docs: no "Co-Authored-By" lines for a model, no "Generated with" footers, no bot signatures. The harness refuses them before a push.
- **Guards and policy are sensitive.** Changes to `rules/policy.json`, `packs/core/vendor/guards/`, `engine/nodaris_harness/policy.py` or the approval code need an explicit review by a maintainer and a policy version bump.
- **Outside work is rebuilt, not vendored.** Follow the `study-and-rebuild` skill and keep licence credit.
- **Product text** (installer screens, doctor output, errors, docs) follows `packs/core/skills/plain-copy/SKILL.md`: professional English, sentence case, "click" not "press".
- The rules the harness gives every agent are in `rules/RULES.md`; they apply here too.
