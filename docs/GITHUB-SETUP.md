# GitHub setup

The commands that configure `NodarisAI/Coding-Agent_Harness`. A maintainer runs them once, after the `tests` workflow has run at least once (GitHub only offers a status check as required after it has reported).

Branch protection on a private repository needs a paid GitHub plan for the organisation (Team or Enterprise). If the API answers `Upgrade to GitHub Pro or make this repository public`, the plan does not include it.

## 1. Description and topics

```
gh repo edit NodarisAI/Coding-Agent_Harness \
  --description "Rules, checks and skills that make any coding agent build healthcare software safely." \
  --add-topic coding-agents \
  --add-topic claude-code \
  --add-topic codex \
  --add-topic developer-tools \
  --add-topic healthcare \
  --add-topic hipaa \
  --add-topic security
```

## 2. Merge settings (linear history)

```
gh repo edit NodarisAI/Coding-Agent_Harness \
  --enable-merge-commit=false \
  --enable-squash-merge \
  --enable-rebase-merge \
  --delete-branch-on-merge
```

## 3. Protect `main`

Requires one approving review, the `tests` check, linear history, no force pushes and no deletions, for administrators too.

```
gh api --method PUT repos/NodarisAI/Coding-Agent_Harness/branches/main/protection --input - <<'EOF'
{
  "required_status_checks": { "strict": true, "contexts": ["tests"] },
  "enforce_admins": true,
  "required_pull_request_reviews": {
    "required_approving_review_count": 1,
    "dismiss_stale_reviews": true,
    "require_code_owner_reviews": false
  },
  "restrictions": null,
  "required_linear_history": true,
  "allow_force_pushes": false,
  "allow_deletions": false,
  "required_conversation_resolution": true
}
EOF
```

GitHub does not let an author approve their own pull request. With one required review and `enforce_admins` on, every pull request needs a second person with write access. While there is only one maintainer, either add a reviewer account or set `enforce_admins` to `false` so the owner can merge. `require_code_owner_reviews` is off for the same reason; turn it on when there is a second maintainer.

## 4. Check

```
gh repo view NodarisAI/Coding-Agent_Harness --json description,repositoryTopics,mergeCommitAllowed,squashMergeAllowed,rebaseMergeAllowed,deleteBranchOnMerge
gh api repos/NodarisAI/Coding-Agent_Harness/branches/main/protection \
  --jq '{checks: .required_status_checks.contexts, reviews: .required_pull_request_reviews.required_approving_review_count, linear: .required_linear_history.enabled, force: .allow_force_pushes.enabled, delete: .allow_deletions.enabled}'
```

Expected: `checks` is `["tests"]`, `reviews` is `1`, `linear` is `true`, `force` and `delete` are `false`.

## 5. Team data repository (for sync)

`nodaris-harness sync` pushes to a separate private repository. Create it once:

```
gh repo create NodarisAI/harness-team-data --private \
  --description "Redacted lessons, learner changes and anonymous counts from opted-in team members."
```

Give team members who opt in write access, and limit reading the data to maintainers where the plan allows. GitHub cannot restrict a person with write access to one branch, so each member's pushes are confined to `team/<handle>` by the sync command, not by GitHub. See [TEAM-DATA.md](TEAM-DATA.md).
