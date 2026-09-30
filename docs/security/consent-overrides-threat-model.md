# Threat model: consent overrides (1.2.0)

## What changed
- `engine/nodaris_harness/policy.py`: signed pending records, the approval question, `mark_asked`, `record_consent`,
  terminal-only rules, the pending signature check in `approve`.
- `engine/nodaris_harness/dispatch.py`: every stop (guards, branch rule, prohibited, consequential) writes a pending
  record and offers approval; AskUserQuestion handling in PreToolUse and PostToolUse.
- `engine/nodaris_harness/events.py`: `permission_mode` and `tool_use_id` read from the hook payload; the "ask"
  decision rendered for Claude Code only.
- `rules/policy.json`: `C-COLLAB`, `"consent": "terminal"` on `P-HOOK-BYPASS` and `P-APPROVAL-STORE`, the pending
  folder added to `P-APPROVAL-STORE`.

## Trust boundary
The person is the only party who may approve. The agent (the main session, a subagent, or a prompt injection that
steers either) writes tool calls and their inputs, and can read and write files as the same operating-system user
except where the policy and the deny rules stop it. Claude Code, not the agent, produces hook payloads, the tool call
id and the AskUserQuestion answers.

## Callers
- **The person** in the desktop app or a terminal: answers approval questions, runs `nodaris-harness approve`.
- **The main agent**: sends the approval question; can try to forge or reuse consent.
- **Subagents**: get the terminal route only; the approval question is not offered inside a subagent.
- **Other hosts** (Codex, Gemini, Cursor, OpenCode): terminal route only; the "ask" decision is rendered as a refusal.
- **Background jobs** (the learner, team sync): make no tool calls through these hooks and are not affected.

## Abuse cases and the tests that cover them (engine/tests/test_consent.py)
| # | Abuse | Defence | Test |
|---|---|---|---|
| 1 | The agent fills in `answers` so no question is shown | PreToolUse refuses an approval question with answers or annotations | `test_pre_filled_answers_are_refused_before_the_question_is_shown` |
| 2 | Hooks fail or are skipped on the question, and a pre-filled answer reaches PostToolUse | An answer counts only with the signed "asked" marker written by PreToolUse | `test_an_answer_the_pre_hook_never_saw_approves_nothing` |
| 3 | The marker from one question is reused for another tool call | The marker is keyed and signed by tool call id and removed on use | `test_the_asked_marker_is_bound_to_its_tool_call` |
| 4 | An answer in another session approves this one's action | Session must match the pending record and the marker | `test_another_sessions_answer_is_ignored` |
| 5 | The agent rewords the question to hide the action | The answer's question must equal the harness's text exactly | `test_an_edited_question_approves_nothing` |
| 6 | The agent edits the pending record (benign reason, different action) | Pending records are signed and their hash recomputed, in the app and at the terminal | `test_an_edited_pending_record_approves_nothing_in_the_app_or_the_terminal` |
| 7 | A one-time approval is replayed | Approvals are used once and bound to the exact call | `test_a_stopped_push_offers_the_question_and_one_allow_runs_exactly_one_call` |
| 8 | "Allow for this session" is used on a per-call rule | Only grantable rules accept it | `test_a_session_answer_only_counts_for_a_grantable_rule` |
| 9 | An app click weakens the approval mechanism | `P-HOOK-BYPASS` and `P-APPROVAL-STORE` offer no question | `test_rules_that_protect_the_approval_mechanism_are_approved_only_at_the_terminal` |
| 10 | The agent writes a pending record directly | The pending folder is in the protected approval store | `test_the_pending_folder_is_now_part_of_the_protected_approval_store` |

Each defence in rows 1, 3, 5, 6 and 9 was removed in turn and the named test failed (checked 2026-09-30).

## Residual risk
- The approval key is readable by the same operating-system user; the deny rules and `P-APPROVAL-STORE` keep the
  agent away from it, as before. The managed-settings step on each machine is what locks it.
- A person can be persuaded to click "Allow once". The question states the action, the rule and the reason in full,
  and actions too long to show (over 1,500 characters) go to the terminal.
- For a file write, the question names the file, not its contents; the approval is still bound to the exact contents
  by hash.
