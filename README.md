# Nodaris coding-agent harness

**Your coding agent, with the habits of a senior engineering team.** The harness installs into the coding agent you already use (Claude Code, Codex, Gemini CLI, Cursor or OpenCode) and changes how it works on every task: it plans before it edits, proves its work before it calls it done, keeps secrets and personal data where they belong, asks before anything it cannot undo, and remembers what went wrong last time. It is built for any software: web apps, services, scripts, websites and video. Teams that handle patient or payment data get extra checks on top.

For a one-page tour, with the benchmark results against a plain coding agent, open [`docs/site/index.html`](docs/site/index.html) in a browser.

## What changes when you use it

| You ask for | Plain agent | With the harness |
|---|---|---|
| "Fix the export button" | Edits the first file that looks right and says it is fixed | Finds the cause, writes a failing test, fixes it, runs the checks, and shows you the passing output |
| "Add sign-in to the admin page" | Writes the feature in one pass | Writes a short spec and threat model first, builds it test-first, and has a separate reviewer check the diff |
| "Clean up the old build folders" | Runs `rm -rf` | Moves them to a trash you can restore from |
| "Deploy it" | Runs the deploy | Stops and asks you, in the app or in your terminal, to approve that exact command once |
| A long session with helper agents | Helpers work without your context and spend tokens freely | Helpers get the project's rules and design system, report back at checkpoints, and stay inside a token budget you can see in a live panel |
| The same mistake next week | Makes it again | Recalls the lesson it recorded, and shares it with your team if you opted in |

## What it does for you

- **Plans, then builds.** Each request is routed to the right procedure (bug fix, feature, investigation, security check, release, video) with the steps in order.
- **Proves it.** A coding turn cannot end until a check passed after the last edit, and `nodaris-harness receipt` prints what a session proved for people who do not read code. For a long job, `nodaris-harness ready --arm` keeps the agent working until an acceptance list passes.
- **Keeps you safe without slowing you down.** Ordinary work runs untouched. Reading secret files, writing a real key into code, hidden commands and pushes to protected branches are stopped; pushes, deploys and messages wait for your approval; recursive deletes go to a trash. The harness is yours: any stop can be overridden once you have read what the action is and why it was stopped, by answering the approval question in Claude Code or with `nodaris-harness approve` in your terminal.
- **Protects personal data.** Personal and patient identifiers are replaced with realistic stand-ins before a log, error or file leaves your machine. (Patient data, called PHI, may only go to a model under a business associate agreement, the contract US health law requires.)
- **Learns.** Lessons from mistakes are recalled when the same situation returns; a background learner adapts to how you work, and every change it makes is shown and reversible.
- **Understands the codebase.** Optional code graphs let the agent answer "what calls this" and "what breaks if this changes" without reading every file.
- **Shows what it is doing.** A live side panel and status line show tokens spent, helper agents, recalled memories and files touched.
- **Sets itself up once.** A short, animated onboarding on first use, then never again unless you ask.

A hook is a small command your coding agent runs before and after each action; the harness uses hooks to check each action as it happens.

```
  you ──► your coding agent ──► action ──► harness hook ──► routine: runs and is recorded
                                                  │        ├► consequential: waits for your approval
                                                  │        └► prohibited: stopped with the reason; you can still allow it once
                                                  └► the right procedure, lessons and checks added to the agent's context
```

## Who it is for

Nodaris AI staff and contractors, and partners Nodaris AI authorises in writing. The installer asks whether you are on the Nodaris team: a yes turns on the healthcare pack and offers team sync and the company's own context plugin; a no installs the harness on its own, with nothing shared.

## Install in three steps

1. Check that you have Python 3.9 or later, git and a coding agent.
2. Clone the repository: `git clone https://github.com/NodarisAI/Coding-Agent_Harness.git ~/.nodaris-harness-src`
3. Run the installer and answer its questions: `cd ~/.nodaris-harness-src && python3 install.py`

**Nodaris team members:** follow [docs/TEAM-SETUP.md](docs/TEAM-SETUP.md). It covers the sign-ins you do yourself (Claude, GitHub, AWS), the Jev team key (or Laya, the local alternative in [docs/LAYA.md](docs/LAYA.md)), restarting Claude Code in the desktop app and on the command line, and how pushes and reviews work.

The installer asks a few questions (your company, how you will use it, which agents to connect, how you want replies written), shows the exact changes for each agent, and installs only after you agree. It then runs the doctor to prove the install works. See [INSTALL.md](INSTALL.md) for every platform and for troubleshooting.

## Let your coding agent install it

Open your coding agent and paste this sentence:

> Install the harness from https://github.com/NodarisAI/Coding-Agent_Harness

The agent follows [AGENTS.md](AGENTS.md): it explains what will change, asks your permission, asks the setup questions, installs and runs the doctor. It never installs anything you have not agreed to.

## What it changes on your machine, and how to undo it

- **Your agent's configuration:** hook entries in its settings file (for example `~/.claude/settings.json` or `~/.codex/hooks.json`) and a rules file (`CLAUDE.md`, `AGENTS.md` or `GEMINI.md`). Existing content is kept; a backup is taken first.
- **Skills and agents:** copied into the agent's skills folder.
- **Harness home:** `~/.nodaris-harness` holds approvals, recorded sessions, lessons, your profile and install backups. It never leaves your machine unless you opt in to team sync.
- **Repositories you work in:** shared lessons in `.nodaris-harness/lessons.jsonl`, committed only if you choose to.

To undo: `python3 install.py --uninstall` removes the harness from every agent it was installed in, and each settings file is restored from the backup taken at install. To remove it from one agent, run `bin/nodaris-harness uninstall --host claude`.

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
| `nodaris-harness enforce off` | Turns every harness stop into a warning, so nothing waits on the harness; `enforce relax RULE...` does it for named rules only (the rule names appear in each stop message), `enforce on` restores the stops |
| `nodaris-harness approve HASH` | Approves one waiting action, in your own terminal (`approve --list` shows what is waiting). In Claude Code you can also answer the approval question the agent shows you |
| `nodaris-harness redact FILE` | Prints the file with patient identifiers replaced by stand-ins |
| `nodaris-harness scan` | Checks the changed files for secrets, risky patterns and vulnerable dependencies |
| `nodaris-harness receipt` | Shows what the last session proved |
| `nodaris-harness security check` | Runs a security assessment of the current repository |
| `nodaris-harness lessons list` | Shows the lessons the harness will recall |
| `nodaris-harness learn status` | Shows what the harness has learned about how you work, and lets you revert any change |
| `nodaris-harness tips` | Suggestions drawn from your recent sessions |
| `nodaris-harness policy --explain "git push origin dev"` | Shows how a command would be classed |
| `nodaris` (any `claude` option works, such as `--continue`) | Starts Claude Code with the harness and the live token panel beside it, following exactly that session |
| `nodaris-harness watch` | The live panel on its own, for one session: what the agent is doing now, tokens used this request and since the session was opened, context and cache, suggestions such as /compact or /clear, a ten-minute token timeline, subagents as they run, the memories pulled for your last request, files touched, and your other sessions in a block of their own. Claude Code asks at the start of each session whether to open it |
| `nodaris-harness usage` | Tokens used today and over the last seven days, across every session |
| `nodaris-harness statusline` | One-line version of the same numbers for Claude Code's status line; added at install only if you have none of your own, removed at uninstall |
| `nodaris-harness trash <paths>` (`--list`, `--restore ID`, `--empty DAYS`) | Reversible delete: moves files to the harness trash instead of removing them |
| `nodaris-harness budget --add N` | Raises your per-session subagent token budget |
| `nodaris-harness laya on`, `off`, `status` | Uses Laya, a decision model on your own machine, to read prompts instead of Jev; no key needed. See [docs/LAYA.md](docs/LAYA.md) |
| `nodaris-harness ready` (`--arm` to loop) | Are we done? Runs the repository's acceptance list and names what fails and what waits on a person |
| `nodaris-harness graph` (`--install` first) | Builds the code graphs for the current repository |
| `nodaris-harness onboard` | Runs the onboarding again, for example to change packs or turn team sync on |
| `nodaris-harness sync --dry-run` | Shows what team sync would share, without sending it |
| `nodaris-harness team-intake` | For maintainers: merges team members' shared lessons into the memory vault for review |

If `nodaris-harness` is not on your path, run it as `~/.nodaris-harness-src/bin/nodaris-harness`.

## Privacy

Everything the harness records stays in `~/.nodaris-harness` on your machine: recorded sessions, approvals, your profile and personal lessons. Secrets are never read. Real patient data is never sent to a model without a business associate agreement, and redaction never writes the real values anywhere.

Members of the Nodaris team can opt in to team sync. Once a day it shares redacted lessons, learner changes and anonymous counts with the Nodaris memory vault, and brings the rest of the team's lessons back into your recall. It never shares code, prompts, file contents or patient data. See [docs/TEAM-DATA.md](docs/TEAM-DATA.md).

## More

- [INSTALL.md](INSTALL.md): full installation, update, removal and troubleshooting
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): how the engine, hooks, policy and gates fit together
- [rules/RULES.md](rules/RULES.md): the rules the agent follows
- [docs/PRD.md](docs/PRD.md) and [docs/SPEC-v3.md](docs/SPEC-v3.md): requirements and specification
- [SECURITY.md](SECURITY.md), [CONTRIBUTING.md](CONTRIBUTING.md), [docs/TEAM-DATA.md](docs/TEAM-DATA.md), [CHANGELOG.md](CHANGELOG.md)

Proprietary to Nodaris AI; see [LICENSE](LICENSE). Nodaris AI staff, contractors and people Nodaris AI authorises in writing may use it for Nodaris AI work. Credits for the open-source projects it adapts or learned from are in [NOTICE.md](NOTICE.md).
