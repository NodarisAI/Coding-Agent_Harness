# Installing the harness

## Prerequisites

- Python 3.9 or later (`python3 --version`). The engine uses the standard library only; nothing else is installed.
- git (`git --version`).
- At least one coding agent: Claude Code, Codex CLI, Gemini CLI, Cursor or OpenCode. Any other agent is covered by git hooks only.
- Read access to the private repository `NodarisAI/Coding-Agent_Harness`.

## Platforms

- **macOS and Linux:** follow the steps below in a terminal.
- **Windows:** install WSL (`wsl --install` in PowerShell as administrator, then restart), open the Ubuntu terminal and follow the Linux steps. Run your coding agent inside WSL too, so it reads the configuration the harness writes there.

## Steps

1. Clone the repository into the standard location:

   ```
   git clone https://github.com/NodarisAI/Coding-Agent_Harness.git ~/.nodaris-harness-src
   cd ~/.nodaris-harness-src
   ```

2. Run the installer:

   ```
   python3 install.py
   ```

   It asks for your company name, whether you are on the Nodaris team, how you will use it (healthcare apps, websites, video and motion, or general software), which coding agents to connect, how you want replies written and your plan. It then shows the exact changes for each agent and installs only after you agree. Useful options: `--dry-run` (show the changes, write nothing), `--host claude` (one agent), `--packs core,healthcare`, `--no-motion`, `--yes --answers '<json>'` (unattended).

   In the Claude Code desktop app you can also run `nodaris-harness onboard`, or let the harness ask the same questions as clickable choices at your first session.

   Hosts: `claude`, `codex`, `gemini`, `cursor`, `opencode`, `git`.

3. Verify:

   ```
   bin/nodaris-harness doctor --host claude
   ```

   Every line should read `pass`. For Claude Code, `--live` also runs a real headless session and checks that a secret is not revealed and the session was recorded.

## Update

```
cd ~/.nodaris-harness-src
git pull
python3 install.py --reconfigure
```

`--reconfigure` asks the setup questions again with your previous answers filled in.

## Uninstall

```
python3 install.py --uninstall              # every connected agent
bin/nodaris-harness uninstall --host claude   # one agent
```

Uninstall restores each changed file from the backup taken at install. Your harness home, `~/.nodaris-harness`, is kept so recorded sessions and lessons are not lost; delete it yourself if you want them gone.

## Troubleshooting

| Problem | What to do |
|---|---|
| `python3: command not found` or a version below 3.9 | Install Python 3.9 or later from python.org or your package manager, then open a new terminal. |
| `Repository not found` when cloning | Your GitHub account does not have access to the private repository. Ask a maintainer to add you, then run `gh auth login`. |
| `nodaris-harness: command not found` | Run it by path: `~/.nodaris-harness-src/bin/nodaris-harness`. |
| The doctor reports `MISSING` for a file | Run the install for that agent again. |
| The doctor reports `FAIL` on a probe | Check that no other tool rewrote the agent's hook settings, then reinstall. If it persists, open an issue with the doctor output. |
| An action is stopped with a hash | The harness wants your approval. In Claude Code, answer the approval question the agent shows you; anywhere, type `nodaris-harness approve HASH` in your own terminal. |
| `Approval must be given by a person in their own terminal` | Approvals cannot come from the agent's shell. Open a separate terminal window and run the command there. |
| The harness moved to another folder | Reinstall; hook commands carry the absolute path of the clone. |
| Codex, Gemini, Cursor or OpenCode behaves differently from Claude Code | Those hosts are contract-tested, not live-verified. OpenCode has no prompt or stop hooks, so there the request router, the prompt personal-data check and prompt-time lesson recall do not run, and the done gate and the acceptance loop cannot hold a turn open; the guards, approvals and edit-time checks still apply. See `docs/ARCHITECTURE.md`. |
