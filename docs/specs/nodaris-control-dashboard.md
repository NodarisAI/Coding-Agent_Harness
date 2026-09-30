# Spec: the Nodaris control dashboard (the harness as SMCP for coding agents)

Status: draft for Varun's go-ahead, 2026-09-30. Nothing here is built yet beyond the token panel of 1.1.0.

## The ask (Varun, 2026-09-30 02:32, in his words)
"This token dashboard should actually be something of what SMCP would do to see what your agent actually does ...
we're seeing the toll ... its agent gates ... visually represented ... any requests that are crazy ... come on the
token dashboard ... to say yes or no or give approval ... the user can stop at any point ... a little Nodaris pet ...
we don't need the thinking processes. We just need to see what the agents are doing."

## What it is
The harness already sits in every tool call like SMCP sits in front of a model: it classifies each call (routine,
consequential, prohibited), stops some, records all. The dashboard makes that visible and puts the person's controls
in one always-open terminal window:
1. **Agent map.** The main agent and every subagent as a live tree: the tool each is running, its timer, tokens, and
   the last five actions (tool and target only; no thinking, no file contents).
2. **Gate stream.** Every harness decision as it happens: allowed, sent back, refused, waiting for approval, with the
   rule name and one line of why. Counts per rule for the session.
3. **Approvals.** A consequential call waiting for approval appears as a card: the exact action, the rule, the
   working directory. The person approves or refuses from the dashboard.
4. **Stop.** Select an agent and stop it: the harness refuses that agent's next tool calls with "stopped by the
   person", which ends it within one call. A stop for the whole session refuses every call until resumed.
5. **Tokens and suggestions.** The 1.1.0 panel, kept: this session since it opened, context, cache, suggestions.
6. **The pet.** A small Nodaris character in the corner whose state follows the session: idle, working, waiting for
   you (an approval), blocked (a refused call), done. It is information, not decoration, and it stops moving under
   reduced motion.

## The hard problem: an approval the agent cannot give itself
Today an approval is typed in the person's own terminal (`nodaris-harness approve HASH` reads /dev/tty). In the
desktop app the dashboard runs in the app's Terminal panel, which the agent can both read and type into with its
terminal tools. An approve key there would let the agent approve its own consequential call. Options:
- **A. Approvals only in a window the agent cannot reach.** The dashboard shows the card and says "approve in the
  approval window"; the harness opens a separate Terminal.app window (not the app's panel) running the existing
  approve prompt. The agent has no tool for that window (computer use is off for terminals by tier). Keeps the
  existing trust model. Recommended.
- **B. Approve in the dashboard with a one-time code sent out of band** (email or phone). Strong, but Telegram is
  down and email needs an outbound service.
- **C. Approve in the dashboard only when it runs in a tmux pane on the command line**, never in the desktop app.
  Simple, but splits behaviour by host.
Recommendation: A everywhere, plus C as a convenience on the command line. This changes approval code, which
AGENTS.md marks as needing a maintainer review and a policy version bump, so it waits for Varun's yes.

## Data it needs (all local, read-only except stop and approve)
- Transcript and subagent transcripts (already tailed by the panel).
- Harness decisions: `trajectory` records and `signals/<session>.jsonl` (already written per call).
- Pending approvals: `<home>/pending/*.json` (already written by the gate).
- Stop requests: a new `<home>/stop/<session>.json` read by the PreToolUse hook. The file is written only by the
  dashboard and is honoured only for its own session id.

## Build slices, riskiest first
1. **Stop** (PreToolUse reads the stop file; dashboard key writes it). Check: a stopped subagent's next call is
   refused with the stop reason; other sessions unaffected; resuming clears it.
2. **Approval window (option A)**. Check: a pending card opens the separate window; approving there lets exactly
   that call through once; typing into the dashboard cannot approve.
3. **Gate stream and agent tree** from the existing records. Check: a refused call appears within one second with
   its rule; the tree shows nested subagents live.
4. **Layout and the pet**: one screen with no scrolling at 100x30 and above; the pet's five states; reduced motion.
   Check: rendered frames at three sizes looked at, like the 1.1.0 panel.
5. **The desktop hook**: at session start, offer to open the dashboard (done in 1.1.0 for the token panel; extend it).

## Out of scope
Showing the model's thinking; editing prompts; approving from a phone (until Telegram or another channel is back).

## Team notices and running without Varun's computer (asked 2026-09-30 02:23)
- **Who is connected.** Today the only reliable signal is a teammate's `agent/team-memory/<handle>` branch in the
  memory vault, written by team sync once a day at session start, and team sync is opt-in. On 2026-09-29 no teammate
  branch existed. Proposal: the installer asks team members to turn team sync on, and the first sync writes a
  `team/<handle>/installed.json` (version, hosts, date; no prompts) so an install is visible within a day.
- **Telling the team about a change.** Proposal: each release gets a GitHub release with the CHANGELOG section as its
  notes; teammates who watch the repository get GitHub's own email. Inside the harness, SessionStart compares the
  installed version with the latest release tag once a day and shows one line: "Harness 1.2.0 is available: <one
  line>. Run `nodaris-harness update`." No new email service, no addresses stored by the harness.
- **Updating by itself.** Proposal: `nodaris-harness update` (git pull of the source plus `install.py --reconfigure
  --yes` with the saved answers), offered, not automatic, because an update changes hooks on a person's machine.
- **Running when Varun's computer is off.** Team intake (merging teammates' vault branches) could run as a scheduled
  GitHub Action on the vault repository instead of on Varun's Mac. That is a CI change on a Nodaris repository, which
  needs Varun's explicit go-ahead.
