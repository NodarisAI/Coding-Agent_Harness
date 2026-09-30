@AGENTS.md

## Notes for Claude Code

- Ask the permission and onboarding questions with the AskUserQuestion tool, one question per call where the choices differ.
- In the Claude Code desktop app, the harness asks the onboarding questions itself at the first session after install; do not ask them twice.
- The doctor's `--live` check starts a headless `claude` session. Run it only if the person agrees, since it uses their quota.
- Do not add a `Co-Authored-By` trailer or a "Generated with Claude Code" line to any commit or pull request in this repository.
