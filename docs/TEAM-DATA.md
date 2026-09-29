# Team data: what `nodaris-harness sync` shares

`nodaris-harness sync` (`--dry-run` shows exactly what would be shared, without sending it; `--days N` limits how far back it looks) pushes a bundle to your own branch. It never creates the team data repository — a maintainer creates that first.

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

A private repository, `NodarisAI/harness-team-data`, on a branch named `team/<handle>`, where `<handle>` is the name you choose at setup. Only harness maintainers can read that repository. Each push is an ordinary git commit, so you can see exactly what was sent in your branch's history.

## How to turn it off

Run `python3 install.py --reconfigure` and answer no to sync. Nothing is sent after that. To have your branch deleted, ask a maintainer.

## How maintainers use it

- **Learner reviews:** changes that several people's learners proposed independently are reviewed and, when they hold up, made part of the shipped rules or router.
- **Benchmark:** recurring failures become benchmark tasks with hidden rubrics, so each release is measured against them.
- **Training set, later:** redacted lessons and accepted or reverted learner changes may become part of a training dataset for a Nodaris model. Nothing customer-derived is used until a contract allows it.

## Retention

Data is kept while you are on the team and for as long as the maintainers need it for the uses above. When you leave the team, or ask, your `team/<handle>` branch is deleted. Material already merged into shipped rules or a dataset stays, since it no longer identifies you.
