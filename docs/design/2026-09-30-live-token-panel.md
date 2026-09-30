# Live token panel: one session, honest numbers, live subagents, and a line in the app

Date: 2026-09-30. Files: `engine/nodaris_harness/{monitor,usage,statusline,launch,dispatch,events,memory}.py`.

## Problem
Varun ran `nodaris` for an 8-turn CLI session and the panel said "70.1M tokens this session". Three faults:
- The panel watched the newest transcript in the folder; his desktop-app session in the same folder was newer, so the
  panel showed that session.
- "Tokens" added cache re-reads (the whole conversation re-sent every turn, billed at about a tenth) to real work;
  98% of the 70M was re-reads.
- Subagents showed 0 until they finished, because only the final tool result was read. Workflow-launched subagents
  never finished at all in the panel.
He also could not read meaning into the flame animation, saw lessons that had nothing to do with his request, and
wanted a token read-out in the desktop app, where no side panel exists.

## Options

### A. Pin the session with a link written by the SessionStart hook
`nodaris` generates a random 16-hex link, sets `NODARIS_PANEL_LINK` for Claude Code and passes `--link` to the panel.
The harness's SessionStart hook (which already runs in every session) writes the session's transcript path to
`<home>/live/<link>.json`. SessionStart also fires on /clear, /resume and compaction, so the panel follows the
session across all of them. Only sessions started by `nodaris` carry the variable.

### B. Pass `--session-id <uuid>` to Claude Code and watch that id
Works for a fresh session only; `--continue` and `--resume` choose their own id, and /clear starts a new one.

### C. Pick the newest transcript whose entrypoint is `cli`
Breaks with two CLI sessions in one folder and says nothing about the desktop app.

## Chosen
Option A, with the old newest-in-folder behaviour kept as the fallback for a bare `nodaris-harness watch`, and the
session's title, app and id always shown so the person can see which session it is.

Numbers: "tokens used" = new input + cache writes + output, subagents included, everywhere (panel, status line, the
end-of-turn line, `usage`). Cache re-reads are always shown next to it, never added. The subagent budget bar reads the
gate's own charged total (capsule state, which weights re-reads at a tenth) plus running subagents, so the bar and the
gate agree.

Subagents: the panel tails `<session>/subagents/agent-*.jsonl` and maps each to its launch through
`agent-*.meta.json` (`toolUseId`). A subagent whose own last message ended its turn is finished; one silent for 30
minutes is shown as stalled rather than running.

Across sessions: `usage.py` keeps a ledger (numbers, session ids, 12-hex message-id hashes; mode 0600) of the last eight
days, read incrementally by byte offset with a non-blocking lock. First build on this machine: 6 s for 2.6 GB; an update
after that: under 0.1 s. SessionStart refreshes it in a detached process; the Stop hook gives it 0.25 s.

The app: the Stop hook returns the line as Claude Code's `systemMessage` (Claude Code only; other hosts unchanged), and
only when every gate allowed the stop. `turn_summary: false` turns it off.

Animation: every moving part carries a value. The spinner and timer run only while the model or a subagent works; the
timeline scrolls in 10-second slices with prompts marked; numbers count up to their new value with a fading "+N".

Recall: a lesson needs two shared words, one a keyword or part of its situation; words common to many lessons count
less (inverse document frequency); the matched words are printed with the lesson. Varun's personal ai-os repo card now
keeps a repo lesson only when it overlaps the prompt (`recall.repo_card(repo, prompt=...)`).

## Rejected
- B and C above.
- Counting cache re-reads at a tenth inside the headline: correct for cost, but no one can check it against Claude
  Code's own numbers. Two plain figures side by side are easier to trust.
- A SessionEnd notice: Claude Code does not show SessionEnd output, and the desktop app has no session end.
- Re-reading every transcript on each Stop: 2.6 GB a week makes that seconds per turn.

## Acceptance checks
- `engine/tests/test_live_panel.py`: a linked panel ignores a newer session in the same folder; a bad link id is
  refused; SessionStart records the link only when `nodaris` started the session; re-reads stay out of "used" and out
  of the timeline; the request figure starts at the last human prompt and ignores notifications, command echoes and
  compaction summaries; a running subagent's tokens grow as its transcript grows; nested, finished and stalled
  subagents are shown correctly; the three recall formats are grouped by request with why they matched; the Stop
  line reaches Claude Code as `systemMessage`, never on a blocked stop or for other hosts, and can be switched off;
  the ledger counts each message once across repeated lines, copied sessions and reloads, forgets old days, holds no
  text and is written 0600.
- `engine/tests/test_engine.py::test_lesson_recall_needs_the_situation_not_just_shared_words`.
- ai-os `memory_loop/tests/test_core.py::test_repo_card_keeps_only_lessons_that_match_the_prompt`.
- Live check: the panel run in tmux on a real 105 MB session showed the current tool with a running timer, finished
  and stalled subagents, and the memories for the current request.

## Follow-up the same night: one session, from when it was opened
Varun saw 25M "this session" for a desktop conversation resumed over six days, and 98.6M (then 805M, in red) from the
copy installed on his machine, which still added cache re-reads and followed whichever session wrote last.
- Claude Code writes each SessionStart hook result into the transcript as an attachment named `SessionStart:<source>`.
  The panel reads it: the last `startup`, `resume` or `clear` opens the session; `compact` does not. The session counts
  from there, so the figure works for existing transcripts without any new state. Rejected: a harness-written open
  record (misses every session opened before install) and a gap-in-activity heuristic (guesses).
- The status line cache prunes old messages into running totals; a second running total keeps the messages after the
  latest open, reset at each open, so the scoped figure survives pruning.
- Suggestions are computed from the session alone and never run anything: context against the window or the person's
  `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE`, cache lifetime from `cache_creation.ephemeral_1h_input_tokens`, lessons and
  memory notes written during the session (the harness and memory-loop add commands, auto-memory and handoff files),
  stalled subagents and the budget.
- The dashboard offer at SessionStart is context for Claude, not a command: Claude asks with AskUserQuestion and opens
  the panel only on a yes. The session id is checked against `[A-Za-z0-9_-]{8,64}` before it goes into a command.
