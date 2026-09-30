---
name: investigate
description: Use for "how does X work", "why is it built this way", "where should this live", "what happens when", "are we sure", and before changing code whose behaviour or history you have not traced. Also the first step of a bug fix and of the design skill. Answers from evidence with citations, separates what was checked from what is inferred, and never answers from memory of how such systems usually work.
---

# Investigate: answer how and why from evidence

The harness router sends investigation and bug-fix work here. At the end of the turn the harness checks that the
answer carries evidence (a file and line, a command with its exit code, or quoted output) and sends it back once if
it does not. Evidence is the whole point: a fluent answer about how healthcare software usually works is worse than
"not verified", because the person will act on it.

Adapted from the `how` and `why` skills in pstack (MIT), rebuilt for the Nodaris harness: no external data sources
that could carry patient data, and the harness's own lessons and review records as sources.

## 1. Frame (one minute)
- Restate the question in one sentence and say what would count as an answer.
- Name the target: files, symbols, endpoints, tables. If the question is vague, state your reading and continue;
  the person can redirect.
- Decide the size. One module or one function: investigate it yourself in one pass. A subsystem or a cross-cutting
  behaviour: split it into two to four angles (for example the data path, the auth path, the failure path) and give
  each angle to a read-only subagent in one message so they run in parallel. Each returns findings with citations,
  under 400 words.

## 2. How: trace the runtime
- Start from the entry point a user or caller hits (route, command, job, event), then follow the call chain to where
  data is read, changed or sent. Read the ranges, not whole files.
- Run it where you can: a focused test, a command, a query against a local or synthetic database. Quote the decisive
  output and its exit code.
- Record every place that reads or writes the thing in question. The dominant failure in this codebase family is a
  field that is declared, indexed and tested but never written by the live path; check the writer exists.

## 3. Why: recover the reasons
- `git log --follow --oneline -- <file>`, `git blame -L <start>,<end> <file>`, then the full message of the commits
  that shaped the lines. Pull the pull request body and review comments with `gh pr view` when the remote allows.
- Search the repository's decision records (`DECISIONS.md`, `docs/`, `.planning/`, ADRs) and the harness lessons
  (`nodaris-harness lessons recall "<topic>"`).
- If a reason cannot be found, say that no record of it was found. Never invent a motivation.

## 4. Confidence
Label each claim:
- **Verified:** you read the code or ran it in this session; cite it.
- **Inferred:** follows from verified facts; say from which.
- **Not verified:** could not be checked; say why and what would check it.
Repository documents (READMEs, STATE files, old plans) are claims, not evidence, until the code confirms them.

## 5. Answer
Use these sections, dropping any that do not apply:
- **Answer:** two or three sentences that answer the question.
- **How it works:** the path, step by step, each step with a `path:line` citation.
- **Why it is this way:** the recovered reasons, with commit or document citations.
- **Where things live:** a short list of files and what each owns.
- **Risks and gotchas:** what would surprise the next person, including anything that contradicts the question's
  premise.
- **Not verified:** what could not be checked.

If the answer would surprise the next person, record it: `nodaris-harness lessons add`.
