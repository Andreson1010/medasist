---
name: git-workflow
description: |
  Activate this skill for any Git-related task, including:
  - Creating branches, commits, merges, rebases, or pull requests
  - Resolving merge conflicts
  - Writing or reviewing commit messages
  - Setting up branching strategies (GitFlow, trunk-based, etc.)
  - Reviewing git history, diffs, or blame
  - Undoing changes (revert, reset, restore)
  - Tagging releases and managing versions
  - Setting up or auditing .gitignore files
  - Explaining git concepts or troubleshooting git errors
  Trigger keywords: git, branch, commit, merge, rebase, PR, pull request, conflict, push, tag, stash, cherry-pick, bisect
---

# Git Workflow Skill

## Philosophy

Follow **clean, atomic, and traceable** git practices. Every action should leave the repository in a better state than it was found. Prefer clarity over cleverness.

---

## Branch Naming Convention

Use the pattern: `<type>/<short-description>`

| Type        | When to use                              | Example                        |
|-------------|------------------------------------------|--------------------------------|
| `feat/`     | New feature                              | `feat/user-authentication`     |
| `fix/`      | Bug fix                                  | `fix/login-redirect-loop`      |
| `chore/`    | Maintenance, deps, config                | `chore/update-dependencies`    |
| `docs/`     | Documentation only                       | `docs/api-reference`           |
| `refactor/` | Code change without behavior change      | `refactor/extract-auth-service`|
| `test/`     | Adding or fixing tests                   | `test/add-payment-unit-tests`  |
| `hotfix/`   | Urgent production fix                    | `hotfix/payment-gateway-crash` |
| `release/`  | Release preparation                      | `release/v2.1.0`               |

Rules:
- Use **kebab-case** (lowercase + hyphens)
- Keep descriptions short (2–5 words)
- Never use spaces or special characters

---

## Commit Message Format

Follow the **Conventional Commits** specification:

```
<type>(<scope>): <short summary>

[optional body]

[optional footer(s)]
```

### Types
- `feat` — new feature (triggers MINOR version bump)
- `fix` — bug fix (triggers PATCH version bump)
- `docs` — documentation only
- `style` — formatting, missing semicolons, etc. (no logic change)
- `refactor` — code restructure (no feature or fix)
- `test` — adding or updating tests
- `chore` — build process, tooling, dependencies
- `perf` — performance improvement
- `ci` — CI/CD configuration changes
- `revert` — reverts a previous commit

### Rules
- Summary line: **imperative mood**, max 72 chars (`add`, `fix`, `remove`, not `added`, `fixed`)
- No period at the end of the summary
- Body: explain **why**, not what (the diff shows what)
- Breaking changes: add `BREAKING CHANGE:` in the footer

### Good examples
```
feat(auth): add OAuth2 login with Google

fix(cart): prevent duplicate items on rapid click

docs(readme): update local development setup steps

refactor(api): extract pagination logic into helper

feat(payments)!: replace Stripe v2 with Stripe v3

BREAKING CHANGE: PaymentIntent API response shape has changed.
Clients must update to handle the new `client_secret` field.
```

### Bad examples (avoid)
```
fixed stuff          ← vague, past tense
WIP                  ← not a real commit
update               ← no context
FINAL FINAL v3       ← not a commit message
```

---

## Standard Workflows

### Start a new feature
```bash
git checkout main
git pull origin main
git checkout -b feat/my-new-feature
```

### Daily work cycle
```bash
# Stage specific files (prefer over `git add .`)
git add src/components/MyComponent.tsx

# Review what will be committed
git diff --staged

# Commit with a clear message
git commit -m "feat(ui): add loading skeleton to product card"

# Keep branch up to date with main
git fetch origin
git rebase origin/main
```

### Before opening a Pull Request
```bash
# Ensure branch is up to date
git fetch origin
git rebase origin/main

# Review your own changes
git diff origin/main...HEAD

# Verify commit history is clean
git log origin/main..HEAD --oneline

# Push branch
git push origin feat/my-new-feature
```

### Squash messy commits before PR
```bash
# Interactive rebase to squash last N commits
git rebase -i HEAD~<N>
# In the editor: keep first as `pick`, change others to `squash` or `s`
```

### Merge conflict resolution
```bash
# When a rebase or merge hits a conflict:
git status                    # See conflicting files
# Edit files — resolve markers: <<<<, ====, >>>>
git add <resolved-file>
git rebase --continue         # or: git merge --continue

# To abort and start over:
git rebase --abort
```

### Undo mistakes
```bash
# Undo last commit, keep changes staged
git reset --soft HEAD~1

# Undo last commit, unstage changes (keep files)
git reset --mixed HEAD~1

# Discard last commit AND all changes (destructive!)
git reset --hard HEAD~1

# Undo a commit safely (creates a new revert commit)
git revert <commit-hash>

# Discard uncommitted changes to a file
git restore <file>

# Recover a file deleted by mistake
git checkout HEAD -- <file>
```

### Stashing work in progress
```bash
git stash push -m "WIP: form validation logic"
git stash list
git stash pop             # Apply most recent stash
git stash apply stash@{2} # Apply a specific stash
git stash drop stash@{0}  # Delete a specific stash
```

### Cherry-pick a specific commit
```bash
git cherry-pick <commit-hash>
# For multiple commits:
git cherry-pick <hash1> <hash2>
```

### Tagging a release
```bash
# Annotated tag (preferred for releases)
git tag -a v1.2.0 -m "Release version 1.2.0"
git push origin v1.2.0

# List all tags
git tag -l

# Delete a tag locally and remotely
git tag -d v1.2.0
git push origin --delete v1.2.0
```

---

## Pull Request Checklist

Before marking a PR as ready for review:

- [ ] Branch is up to date with `main` (rebased, not merged)
- [ ] All commits follow Conventional Commits format
- [ ] No debug code, `console.log`, or commented-out blocks
- [ ] Tests pass locally
- [ ] New behavior has tests
- [ ] PR description explains **what** and **why**
- [ ] Screenshots or recordings attached (for UI changes)
- [ ] Breaking changes are documented

---

## .gitignore Best Practices

Always ignore:
```
# Dependencies
node_modules/
vendor/

# Build artifacts
dist/
build/
*.min.js

# Environment & secrets
.env
.env.local
.env.*.local
*.pem
*.key

# Editor & OS
.DS_Store
Thumbs.db
.idea/
.vscode/
*.swp
```

Never commit:
- API keys, tokens, passwords
- Local `.env` files
- Binary files that can be rebuilt
- Personal editor configuration

---

## Branching Strategies (Quick Reference)

### GitFlow (for versioned releases)
- `main` — production only
- `develop` — integration branch
- `feat/*` — branches off `develop`
- `release/*` — branches off `develop`, merges into `main` + `develop`
- `hotfix/*` — branches off `main`, merges into `main` + `develop`

### Trunk-Based Development (for CI/CD teams)
- `main` — always deployable
- Short-lived feature branches (< 2 days)
- Feature flags for incomplete work
- Merge frequently, rebase always

---

## Useful Inspection Commands

```bash
# Visual branch graph
git log --oneline --graph --all --decorate

# Show changes in a commit
git show <commit-hash>

# Find who changed a line
git blame <file>

# Search commit messages
git log --grep="keyword"

# Find which commit introduced a bug (binary search)
git bisect start
git bisect bad                # Current commit is broken
git bisect good <hash>        # Last known good commit
# git will checkout commits for you to test
git bisect reset              # End session

# List remote branches
git branch -r

# Clean untracked files (dry run first!)
git clean -nd
git clean -fd
```

---

## Anti-Patterns to Avoid

| Anti-pattern | Why it's harmful | Better approach |
|---|---|---|
| `git add .` blindly | Commits unintended files | Use `git add -p` or stage files explicitly |
| Force-pushing to shared branches | Destroys teammates' history | Only force-push on your own feature branches |
| Giant commits | Hard to review, hard to revert | Keep commits atomic and focused |
| Merging instead of rebasing | Pollutes history with merge commits | Rebase feature branches before merging |
| Committing secrets | Security incident | Use `.gitignore` + pre-commit hooks |
| `WIP` commits in final PR | Unprofessional, hard to review | Squash before opening PR |