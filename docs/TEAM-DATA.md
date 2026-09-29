# Team data: what `nodaris-harness sync` shares

Team sync shares what your harness learned with the Nodaris memory vault, and brings back what the rest of the team learned. Once you opt in, it runs by itself at the start of a session, at most once a day. `nodaris-harness sync` runs it by hand; `--dry-run` shows exactly what would be shared without sending it, and `--days N` limits how far back it looks.

## Who it is for, and consent

Sync is only for members of the Nodaris team, and it is off until you turn it on. The installer asks whether you are on the Nodaris team; if you say yes, it asks separately whether to enable sync. Saying yes to the first question does not turn sync on.

## What it shares

- **Redacted lessons:** the "when, do, why" lessons recorded on your machine, after redaction and after file paths are reduced to repository-relative form.
- **Learner changes:** the changes the self-learning loop made or proposed (for example a new router keyword or a profile adjustment), with the evidence counts behind them and whether you kept or reverted them.
- **Anonymous counts:** how often each playbook, gate and skill fired, how often a gate stopped a turn, check pass and fail counts, and harness version. Counts carry no text.

Everything passes through the same redaction as recorded sessions before it leaves your machine, and a push with a failed or refused redaction verdict is not sent.

## What it never shares

- Source code, diffs or file contents.
- Prompts, replies or recorded sessions (episodes).
- Patient data, real or redacted.
- Secrets, environment files, keys or tokens.
- Your profile, approvals, security scope or security findings.
- Customer names or customer-derived data.

## Where it goes

The memory vault, `NodarisAI/Nodaris-Memory-Vault`, on your own branch `agent/team-memory/<handle>`, where `<handle>` is the name you choose at setup:

- `team/<handle>/lessons/<id>.md`: one page per lesson, with the trigger keywords and the "when, do, don't, why".
- `team/<handle>/telemetry/<date>.json`: the counts and learner changes described above, and how many tokens subagents spent. No text from your sessions.

Each push is an ordinary git commit, so your branch's history shows exactly what was sent. The vault's `team-memory` check runs on every push and refuses a change outside your folder or anything that looks like a secret or a patient identifier.

## How it comes back to you

A maintainer runs `nodaris-harness team-intake`, which takes the well-formed pages from every member's branch, checks them again with redaction and commits them into the vault's `team/` folder on a branch for review. Once that is merged, every member's next sync copies the team's lessons into a read-only library on their machine, and the harness recalls them the same way it recalls your own. Lessons that hold up across several people are promoted into the vault's `platform/traps.md` or the product pages.

## How to turn it off

Run `python3 install.py --reconfigure` and answer no to sync. Nothing is sent after that. To have your branch deleted, ask a maintainer.

## How maintainers use it

- **Learner reviews:** changes that several people's learners proposed independently are reviewed and, when they hold up, made part of the shipped rules or router.
- **Benchmark:** recurring failures become benchmark tasks with hidden rubrics, so each release is measured against them.
- **Training set, later:** redacted lessons and accepted or reverted learner changes may become part of a training dataset for a Nodaris model. Nothing customer-derived is used until a contract allows it.

## Retention

Data is kept while you are on the team and for as long as the maintainers need it for the uses above. When you leave the team, or ask, your `agent/team-memory/<handle>` branch and your `team/<handle>/` folder are deleted. Material already merged into shipped rules or a dataset stays, since it no longer identifies you.
