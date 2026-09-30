# Security

## Reporting a problem

Open a private security advisory on this repository (Security, then Report a vulnerability), or contact a maintainer listed in `.github/CODEOWNERS` directly. Do not open an ordinary issue, and do not include real patient data, secrets or customer information in the report; describe the steps with synthetic data.

## What the harness guarantees

These hold on hosts where the harness has blocking hooks (Claude Code is live-verified; Codex, Gemini CLI and Cursor are contract-tested):

- Every tool call is classed by code from a versioned policy file (`rules/policy.json`), never by a model.
- Prohibited actions do not run from an agent: skipping or disabling hooks, commands the harness cannot parse (such as downloaded code piped into a shell), starting another agent outside the harness, touching the approval store, AI-authorship marks and pushes to protected branches.
- Consequential actions (pushes, publishing, deploys, remote commands, sending data, destructive database statements, installing software, paid models, reading files marked as holding patient data) run only after a person approves that exact call in their own terminal. One approval covers one call with those exact arguments.
- Secret files are not read and destructive commands are refused.
- Redaction replaces identifiers with same-format stand-ins and never writes the real values to disk. A document that cannot be scanned is refused rather than passed through.
- Live security checks run only against hosts listed in a scope file the owner signs per environment; production is excluded unless the owner adds it explicitly.

## What it does not guarantee

- It is not a sandbox. It relies on the host agent calling its hooks. OpenCode has no prompt or stop hooks, and agents with no hooks are covered only by git hooks, so enforcement there is partly advisory.
- The approval key is readable by the same operating system user. It stops an agent, not a determined person on the same account; locking it needs managed or administrator settings on the machine.
- The policy is currently an unsigned draft (`nodaris-harness policy` shows its state).
- Pattern-based detection of secrets and patient identifiers can miss unusual formats. Redaction reports a verdict on every run; read it.
- It does not replace a penetration test, a code review or your own compliance programme.

## Patient data (PHI)

- Real PHI is never sent to a model that has no business associate agreement. Prompts containing identifiers are stopped and handed back with stand-ins.
- Recorded sessions are redacted before they are written, and the redaction verdict is stored with each one.
- Team sync, when a Nodaris team member opts in, shares only redacted lessons, learner changes and anonymous counts; see [docs/TEAM-DATA.md](docs/TEAM-DATA.md).
- No customer-derived data enters any dataset until a contract with that customer allows it.
- Test fixtures must use synthetic data with names that cannot be mistaken for real people.
