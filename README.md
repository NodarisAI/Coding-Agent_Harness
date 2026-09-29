# Nodaris coding-agent harness

The harness is a set of rules, checks and skills that sits inside the coding agent you already use (Claude Code, Codex, Gemini CLI, Cursor or OpenCode). It makes that agent build software the way a careful healthcare engineering team would: it keeps patient data and secrets out of places they should not go, asks you before anything consequential happens, and does not call work done until there is evidence.

## What it does for you

- Stops the agent from reading secrets, running destructive commands or skipping safety checks.
- Asks you to approve each push, deploy, message or other consequential action before it runs.
- Replaces patient identifiers with realistic stand-ins before text leaves your machine.
- Picks the right procedure for each request (bug fix, new feature, investigation, security check, release) so you do not have to know which step comes next.
- Will not let the agent call a change done until a check has passed after the last edit.
- Remembers lessons from mistakes so they are not repeated.
- Prints a receipt of what a session actually proved, for people who do not read code.

```
  you ──► your coding agent ──► tool call ──► harness hook ──► routine: runs and is recorded
                                                     │        ├► consequential: waits for your approval
                                                     │        └► prohibited: refused with the reason
                                                     └► rules, skills, lessons and gates added to the agent's context
```

## Install in three steps

1. Check that you have Python 3.9 or later, git and a coding agent.
2. Clone the repository: `git clone https://github.com/NodarisAI/Coding-Agent_Harness.git ~/.nodaris-harness-src`
3. Run the installer and answer its questions: `cd ~/.nodaris-harness-src && python3 install.py`

The installer asks a few questions (your company, how you will use it, which agents to connect, how you want replies written), shows the exact changes for each agent, and installs only after you agree. It then runs the doctor to prove the install works. See [INSTALL.md](INSTALL.md) for every platform and for troubleshooting.

Note: `install.py` and the onboarding questions are being built now. Until they land, install per agent with `bin/nodaris-harness install --host claude` (or `codex`, `gemini`, `cursor`, `opencode`, `git`).

## Let your coding agent install it

Open your coding agent and paste this sentence:

> Install the harness from https://github.com/NodarisAI/Coding-Agent_Harness

The agent follows [AGENTS.md](AGENTS.md): it explains what will change, asks your permission, asks the setup questions, installs and runs the doctor. It never installs anything you have not agreed to.

## What it changes on your machine, and how to undo it

- **Your agent's configuration:** hook entries in its settings file (for example `~/.claude/settings.json` or `~/.codex/hooks.json`) and a rules file (`CLAUDE.md`, `AGENTS.md` or `GEMINI.md`). Existing content is kept; a backup is taken first.
- **Skills and agents:** copied into the agent's skills folder.
- **Harness home:** `~/.nodaris-harness` holds approvals, recorded sessions, lessons, your profile and install backups. It never leaves your machine unless you opt in to team sync.
- **Repositories you work in:** shared lessons in `.nodaris-harness/lessons.jsonl`, committed only if you choose to.

To undo: `bin/nodaris-harness uninstall --host claude` (once per agent). Files are restored from the backup taken at install. `python3 install.py --uninstall` will do this for every agent once the installer lands.

## Packs

| Pack | Installed | What it adds |
|---|---|---|
| core | always | Safety guards, rules of engagement, redaction, the request router, investigation, design, review and shipping gates, security checks, production readiness, plain product copy |
| healthcare | when you build healthcare software | Healthcare domain rules and a blueprint for new apps that hold patient, claim or practice data |
| creative | when you make websites, video or motion | Motion design direction, scroll motion, WebGL scenes, motion primitives and product films |

## Everyday commands

| Command | What it does |
|---|---|
| `nodaris-harness doctor --host claude` | Proves the install works: a secret read, a destructive command, a hook bypass and a protected push are refused, and an ordinary command runs |
| `nodaris-harness approve HASH` | Approves one waiting action, in your own terminal (`approve --list` shows what is waiting) |
| `nodaris-harness redact FILE` | Prints the file with patient identifiers replaced by stand-ins |
| `nodaris-harness scan` | Checks the changed files for secrets, risky patterns and vulnerable dependencies |
| `nodaris-harness receipt` | Shows what the last session proved |
| `nodaris-harness security check` | Runs a security assessment of the current repository |
| `nodaris-harness lessons list` | Shows the lessons the harness will recall |
| `nodaris-harness learn status` | Shows what the harness has learned about how you work, and lets you revert any change |
| `nodaris-harness tips` | Suggestions drawn from your recent sessions |
| `nodaris-harness policy --explain "git push origin dev"` | Shows how a command would be classed |

If `nodaris-harness` is not on your path, run it as `~/.nodaris-harness-src/bin/nodaris-harness`.

## Privacy

Everything the harness records stays in `~/.nodaris-harness` on your machine: recorded sessions, approvals, your profile and personal lessons. Secrets are never read. Real patient data is never sent to a model without a business associate agreement, and redaction never writes the real values anywhere.

Members of the Nodaris team can opt in to team sync, which shares only redacted lessons, learner changes and anonymous counts. It never shares code, prompts, file contents or patient data. See [docs/TEAM-DATA.md](docs/TEAM-DATA.md).

## More

- [INSTALL.md](INSTALL.md): full installation, update, removal and troubleshooting
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): how the engine, hooks, policy and gates fit together
- [rules/RULES.md](rules/RULES.md): the rules the agent follows
- [docs/PRD.md](docs/PRD.md) and [docs/SPEC-v3.md](docs/SPEC-v3.md): requirements and specification
- [SECURITY.md](SECURITY.md), [CONTRIBUTING.md](CONTRIBUTING.md), [docs/TEAM-DATA.md](docs/TEAM-DATA.md)

This repository is private. Licence terms are in [LICENSE](LICENSE); credits for studied and vendored work are in `packs/core/vendor/pstack/NOTICE.md` and `packs/core/vendor/SOURCE.json`.
