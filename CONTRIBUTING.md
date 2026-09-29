# Contributing

## Branches and pull requests

- `main` is protected. Nobody commits or pushes to it directly, and force pushes and deletions are blocked.
- Work on a branch named `feat/`, `fix/`, `docs/` or `chore/` followed by a short topic, for example `feat/sync-redaction`.
- One pull request per change. Fill in the pull request template, including the test output.
- A pull request needs one approving review and a passing `tests` check. History is linear: squash or rebase, no merge commits.
- No AI authorship marks anywhere: commits, pull request text, code, comments or docs.

## Tests

Every behaviour change comes with a test that fails without it. Run the full suite before opening a pull request:

```
NODARIS_HARNESS_NO_BG=1 python3 -m pytest
```

Suites live in `engine/tests/`, `packs/core/hooks/tests/`, `packs/core/tools/tests/` and `tests/` (see `pytest.ini`). CI runs them on Ubuntu and macOS with Python 3.9 and 3.12. Host adapters are covered by contract tests that feed each host's real payload shape through the dispatcher; install and uninstall must round-trip byte for byte in a temporary home.

The engine is standard library only and must run on Python 3.9. Do not add a dependency.

## Versioning

- Releases are tagged `vX.Y.Z` on `main` following semantic versioning: a major version for a change that breaks an installed setup or the policy format, a minor version for a new command, pack, skill or gate, a patch for fixes.
- Every release adds an entry to `CHANGELOG.md` describing what changed for the person using the harness.
- A change to `rules/policy.json` also bumps the policy `version`. The policy stays an unsigned draft until the owner signs it.

## Adding a pack or a skill

- A skill is a folder under `packs/<pack>/skills/<name>/` with a `SKILL.md` whose frontmatter has `name` and a `description` that says when to use it, in the words a person would use.
- A new pack is a new folder under `packs/` and needs the owner's agreement, since the installer offers it by use case.
- Outside work (a repository, a plugin, a post) is studied and rebuilt as our own with the `study-and-rebuild` skill. Never vendor a project wholesale, never download an unpinned package at use time, and keep licence credit in the pack's notice file.
- Skill text a person reads follows `packs/core/skills/plain-copy/SKILL.md`.

## Sensitive areas

Changes to the policy, the guards (`packs/core/vendor/guards/`), approvals, redaction or the security check need a threat note in the pull request and a maintainer review. See [SECURITY.md](SECURITY.md).
