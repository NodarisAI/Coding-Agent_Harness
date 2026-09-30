# Consent overrides, contact identifiers and a dashboard per session

Date: 2026-09-30. Version 1.2.0, policy 0.3-draft.

## Why

Shashank, the first teammate to use the harness day to day, reported three problems and Varun agreed with each:

1. An email address on its own stopped a prompt as patient information, so ordinary work (a QA login, a vendor
   contact) could not be typed.
2. Every pull request, comment and review needed a separate terminal approval, and the terminal approval did not
   work at all (fixed in his pull request: `/dev/tty` cannot be opened read-write).
3. Varun added, in his words: "the user should be able to override these kind of hooks ... because at the end of the
   day this is their harness ... as long as they give their confirmation ... after understanding". Stops that no one
   could lift, even the owner of the machine, contradicted the rule that the harness never takes a decision from the
   person.

He also reported that the token dashboard showed whichever session in `ai-os` wrote last, not the one in front of
him.

## Options

### A. In-app approval question plus the terminal
The refusal carries an AskUserQuestion payload written by the harness; the person answers in the app; the hooks turn
the answer into a signed one-time approval. The terminal approval stays.

### B. A separate approval window
Open a Terminal.app window for every approval (option A in the control dashboard spec), outside the agent's reach.

### C. Claude Code's native "ask" decision only
Return permissionDecision "ask" and let Claude Code's own dialog decide.

## Chosen
Option A, with Option C kept for C-COLLAB in permission modes that show a dialog. Details below.

## Decisions

**Contact details are context-dependent.** Health identifiers (SSN, MRN, member and claim ids, patient names) still
stop a prompt on their own. Contact details (email, phone, street address, account number) stop it only with a health
cue in the text, next to a health identifier, or three or more at once. The person's own git email, company domains
and reserved test domains never count. Everything that reaches Jev or Laya is still masked.

**Every stop can be approved once, in two places.**
- Terminal: `nodaris-harness approve HASH`, as before, now for prohibited rules, guards and the branch rule too.
- Claude Code: the refusal carries an AskUserQuestion payload the agent sends unchanged. The person answers in the
  app, which shows the question in every permission mode, bypass included (the native permission dialog does not
  appear in bypass mode, which is what Varun and Shashank use).

**The agent cannot answer for the person.** The question is written by the harness from a pending record signed with
the approval key, and the action hash is recomputed from the record. The PreToolUse hook refuses an approval question
sent with `answers` or `annotations` filled in and otherwise records a signed "asked" marker bound to the tool call
id and session. The PostToolUse hook accepts an answer only when that marker exists, the question text is exactly the
harness's, and the session matches. The pending folder joins the protected approval store (`P-APPROVAL-STORE`).

**Two rules stay terminal-only.** `P-HOOK-BYPASS` and `P-APPROVAL-STORE` protect the approval mechanism itself. A
click in the app that weakened the mechanism would compound: every later approval would depend on it. They are still
overridable, at the terminal, where the person reads the full action.

**Pull request and issue collaboration** moved from `C-PUBLISH` to a new rule, `C-COLLAB`, marked `ask` (Claude
Code's own dialog in a prompting mode) and `grantable` (one approval covers the rule, the session and the repository
for up to 12 hours). Merges, releases, pushes and deploys stay per call.

**The dashboard follows its own session.** The SessionStart hook writes a link keyed by the desktop app's session id
(`CLAUDE_CODE_HOST_SESSION_ID`), and the dashboard the session offers to open follows that link, so it moves with
/clear and /resume. A panel started by hand uses `--session`, then the session whose shell started it
(`CLAUDE_CODE_SESSION_ID`), then the newest transcript in the folder, and stays on what it found instead of re-picking
the newest every five seconds.

## Rejected

- Option B, a separate window for every approval: it needs the
  person to switch windows for each comment, and it does not help in the desktop app, where the question can be shown
  in place.
- Option C for everything: Claude Code does not show its dialog in bypass mode.
- Making consent a standing setting ("allow all pushes"): an approval stays bound to one exact call, except for the
  one rule marked grantable.

## Acceptance checks
- A stopped push offers the question; "Allow once" lets exactly that call run once
  (`test_a_stopped_push_offers_the_question_and_one_allow_runs_exactly_one_call`).
- A prohibited action and a guard stop can each be allowed once with consent.
- Pre-filled answers, an answer the PreToolUse hook never saw, another session's answer, an edited question and an
  edited pending record approve nothing (`engine/tests/test_consent.py`).
- `P-HOOK-BYPASS` and `P-APPROVAL-STORE` offer no question and are approved only at the terminal.
- A lone email does not stop a prompt; one next to a health cue does (`engine/tests/test_contact_and_asks.py`).
- The dashboard stays on its own session (`engine/tests/test_panel_session.py`).
- Full suite passes on Python 3.12 and 3.9; the harness security scan is clean.

## Not verified

- That the desktop app's session id stays the same across /clear; if it changes, the dashboard stays on the session
  it was opened for, as before the change.
- A live run in the desktop app: the tests drive the hooks with the payload shapes Claude Code documents
  (`tool_use_id`, `tool_response.answers`, `permission_mode`).
