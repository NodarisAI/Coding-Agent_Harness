# Setting up the harness as a Nodaris team member

This guide takes you from nothing to a working harness in Claude Code, in the desktop app and on the command line. Plan on about fifteen minutes. Each step says how to check that it worked.

## Before you start

You need:

- A Mac or Linux machine with Python 3.9 or later (`python3 --version`) and git.
- A Claude account (Pro, Max or Team) for Claude Code.
- A GitHub account that a Nodaris admin has added to the **NodarisAI** organization.
- An AWS sign-in for the Nodaris account with read access to the team key (a Nodaris admin grants this; see "For admins" at the end).

## 1. Install the tools

On a Mac with Homebrew:

```bash
brew install git gh awscli
curl -fsSL https://claude.ai/install.sh | bash
```

The Claude desktop app is available from claude.ai/download. It uses the same settings as the command line, so there is nothing separate to install for it.

## 2. Sign in

```bash
claude auth login
gh auth login
aws configure sso
aws sso login
```

- **Claude Code:** sign in with your Claude account. Check with `claude auth status`.
- **GitHub:** choose GitHub.com, HTTPS, and sign in with your browser. Check with `gh auth status`.
- **AWS:** use the start URL and region your admin gave you, and name the profile something you will remember. If it is not your default profile, run `export AWS_PROFILE=<name>` before the installer, or give it as `aws_profile` in the answers.

Then tell git who you are, if you have not already:

```bash
git config --global user.name "Your Name"
git config --global user.email "you@nodaris.ai"
```

## 3. Install the harness

```bash
git clone https://github.com/NodarisAI/Coding-Agent_Harness.git ~/.nodaris-harness-src
cd ~/.nodaris-harness-src
python3 install.py
```

Answer the questions. Say yes when it asks whether you are on the Nodaris team, and yes to Jev. The installer:

1. Shows every change it will make to your Claude Code configuration and installs only after you agree.
2. Runs the doctor, which proves that a secret read, a destructive command, a hook bypass and a push to a protected branch are refused, and that an ordinary command runs.
3. Fetches the Jev team key from AWS with your own sign-in and saves it in `~/.nodaris-harness/secrets/jev.key`, readable only by you. The key is never printed and never goes into a repository.
4. Lists anything you still need to set up yourself.

## 4. Restart Claude Code

Claude Code reads its settings when a session starts. A session that was already open keeps working without the harness until you restart it.

- **Command line:** exit the session and start a new one with `claude`. To carry on the conversation you were having, run `claude --continue` (the most recent one) or `claude --resume` (choose from a list).
- **Desktop app:** quit the app completely (Claude, then Quit) and open it again. Your sessions reopen, and each one now runs with the harness.

To confirm it is active, start a session and ask: "Run `cat .env`". The harness refuses the read and explains why.

## 5. Start working

- **Command line:** run `nodaris` in your project folder instead of `claude`. It opens Claude Code with the harness and the live token panel beside it. Any Claude Code option works: `nodaris --continue`, `nodaris --resume`, `nodaris -p "question"`. The panel needs tmux (`brew install tmux` on a Mac); without it, Claude Code still starts and the panel opens in a second window.
- **Desktop app:** open a session as usual. The harness runs in every session, and at the end of each reply a line shows the tokens used: for the request, for the session (with cache re-reads shown separately) and for the day across all your sessions. For the full live panel, open a terminal in the same project folder and run `nodaris-harness watch`.

The installer adds the `nodaris` and `nodaris-harness` commands to `~/.local/bin`. If your terminal says the command is not found, add that folder to your `PATH` (for zsh: `echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc`, then open a new terminal).

## 6. Add the git hooks to each repository you push from

The harness already stops the agent from pushing to protected branches. The git hooks also check the commits and pushes you make yourself, for secrets, patient data and AI-authorship marks:

```bash
cd ~/path/to/the/repository
~/.nodaris-harness-src/bin/nodaris-harness install --host git --project .
```

## 7. Check everything

```bash
~/.nodaris-harness-src/bin/nodaris-harness setup-check
```

Every line should read `ok`. Each `todo` line shows the command that fixes it.

## How pushing works

- Work on a branch named `feat/`, `fix/`, `docs/` or `chore/` followed by a short topic, never on `main`.
- Commit the files you changed by name. The harness refuses `git add -A` and `git add .` in product repositories.
- Push your branch and open a pull request: `git push -u origin feat/my-change`, then `gh pr create`.
- A reviewer approves the pull request on GitHub, and it is merged there. Pushes to `main`, `master`, `staging`, `prod` and `release` branches are refused, by the harness on your machine and by the branch rules on GitHub.
- Deploys, messages and other actions that are hard to undo wait for your approval. In Claude Code the agent asks you with an approval question you answer in the app. In any agent, run `nodaris-harness approve --list` in your own terminal to see what is waiting, and `nodaris-harness approve HASH` to approve one.

## Everyday commands

| Command | What it does |
|---|---|
| `nodaris-harness setup-check` | What you still need to set up yourself |
| `nodaris-harness doctor --host claude` | Proves the install works |
| `nodaris` | Claude Code with the harness and the live token panel beside it |
| `nodaris-harness watch` | The live panel on its own, for the desktop app or a second terminal |
| `nodaris-harness usage` | Tokens used today and over the last seven days, across every session |
| `nodaris-harness jev status` | Whether Jev is on, whether the key is present, and what it has spent today |
| `nodaris-harness jev off` | Stops sending messages to Jev; `jev on` starts again |
| `nodaris-harness receipt` | What the last session proved |

To turn off the token line at the end of each reply, set `"turn_summary": false` in `~/.nodaris-harness/settings.json`.

## Updating and removing

- **Update:** `cd ~/.nodaris-harness-src && git pull && python3 install.py`, then restart Claude Code as in step 4.
- **Remove:** `cd ~/.nodaris-harness-src && python3 install.py --uninstall`. Every file the harness changed is restored from the backup taken at install. Code graph tools you chose to install stay installed; remove them with `uv tool uninstall code-review-graph graphifyy` if you no longer want them.

## For admins: the Jev team key

The key lives in AWS Secrets Manager, never in the repository.

1. In the AWS console, open Secrets Manager in the team's region and choose **Store a new secret**, **Other type of secret**, **Plaintext**. Paste the OpenRouter key as the whole value. Name the secret `nodaris/harness/jev`.
2. Give each team member's role read access to that one secret, and nothing else:

   ```json
   {
     "Version": "2012-10-17",
     "Statement": [{
       "Effect": "Allow",
       "Action": "secretsmanager:GetSecretValue",
       "Resource": "arn:aws:secretsmanager:<region>:<account>:secret:nodaris/harness/jev-*"
     }]
   }
   ```

3. Give the key a monthly spending limit in OpenRouter. The harness also stops calling Jev once a person has spent $0.50 in a day.
4. To rotate the key, update the secret, then ask each person to run `nodaris-harness jev fetch-key`.
