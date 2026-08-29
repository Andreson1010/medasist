---
name: ship-feature
description: End-to-end delivery pipeline for this repo — branch, implement, test, open PR, MANDATORY code review, fix findings, squash-merge. Trigger with /ship-feature, or whenever the user asks to "ship", "deliver", "finish", or "merge" a feature/fix the full way through.
---

# Ship Feature

Seven steps, in order. Do not skip step 5. Do not commit to `main` directly — that is the #1 recurring failure this skill exists to prevent.

---

## 1. Create feature branch

Never work on `main`. Branch off latest `main` first:

```bash
git checkout main
git pull origin main
git checkout -b feature/<short-name>   # or fix/<short-name> for bug fixes
```

Naming: `feature/<name>` or `fix/<name>` (this repo's convention — see existing `origin/feature/*` and `origin/fix/*` branches and AGENTS.md "Git Workflow"). Keep `<short-name>` kebab-case and descriptive. Commits in Portuguese, imperative mood: `feat: adiciona endpoint de consulta RAG`.

If currently on `main` with uncommitted work, branch first (`git checkout -b feature/<name>`), then commit — never commit to `main` and branch afterward.

---

## 2. Implement

Follow the `build-with-tests` skill:
- Match existing patterns (ingestion in `src/medasist/ingestion/`, config in `src/medasist/config.py`, retrieval in `src/medasist/retrieval/`, generation in `src/medasist/generation/`)
- No hardcoded values — everything configurable goes in `src/medasist/config.py`
- Immutable dataclasses (`frozen=True`), functions < 50 lines, nesting < 4 levels
- `from __future__ import annotations` on line 1 of every `.py` file
- `pathlib.Path` always, `logger = logging.getLogger(__name__)` never `print()`
- Write tests alongside the code, not after (`test_*` naming, two styles: module-level functions or `Test*` classes)

Commit with Conventional Commits in Portuguese:
```bash
git add <specific files>
git commit -m "feat(rag): descrição curta da mudança"
```

---

## 3. Run tests

Gate check — must pass before opening a PR:

```bash
pytest tests/ -v --cov=src --cov-fail-under=80
```

- All tests pass
- Coverage ≥ 80% (write more tests if it drops, don't weaken assertions)
- New code has: happy path, empty/edge case, and failure-path tests
- LLM calls mocked — no real calls to LM Studio in the test suite
- Cold start tested — every retrieval function has a test for empty results
- Citations validated — orphan markers and hallucinated `[N]` are stripped

If anything fails, fix it before proceeding. Do not open a PR with a red test suite.

---

## 4. Open PR

```bash
git push -u origin feature/<short-name>

gh pr create \
  --title "<type>(<scope>): <descrição curta>" \
  --body "$(cat <<'EOF'
## O que foi feito
- ...

## Por que foi feito
...

## Como testar
1. ...

## Checklist
- [ ] Testes passam (`pytest tests/ -v --cov=src --cov-fail-under=80`)
- [ ] Cobertura >= 80%
- [ ] Sem credenciais/segredos no diff
- [ ] Lint ok (`ruff check src/ tests/ scripts/`)
- [ ] Formatação ok (`black --check src/ tests/ scripts/`)
- [ ] Regras de segurança médica: disclaimer, cold start, citations, sem dados de paciente
EOF
)"
```

Store the PR URL/number — needed for step 5.

---

## 5. MANDATORY code review

**Not skippable.** This is the step that has been missed before, causing findings to slip into merged code.

Invoke the local **`code-reviewer`** skill against the opened PR diff (`git diff origin/main...HEAD`). Wait for the report before doing anything else. Don't invoke a plugin/cloud review variant (e.g. "ultra") automatically — that's billed and user-triggered only.

Do not proceed to step 6/7 until the review has actually run and you have its findings in hand.

---

## 6. Fix findings

Route findings by severity:

| Severity | Action |
|---|---|
| Critical / Important | Fix now, on the same branch, before merge |
| Minor | Fix now if trivial, otherwise note in PR and let the user decide |

Push fixes as **new commits** (never amend/force-push a PR others may have pulled):

```bash
git add <files>
git commit -m "fix(scope): endereça apontamento do code review"
git push
```

If findings were Critical/Important, re-run step 5 (`code-review`) on the updated diff before moving on.

---

## 7. Squash-merge

Only after the review is clean (or all blocking findings addressed):

```bash
gh pr merge --squash
```

Then sync local `main` (per AGENTS.md "Run `git pull origin main` after merging PRs"):

```bash
git checkout main
git pull origin main
git branch -d feature/<short-name>
```

---

## Hard rules

1. **Branch before commit, always.** If you're on `main`, stop and branch first.
2. **Step 5 is mandatory.** A PR with no code-review report is not done.
3. **Critical/Important findings block merge.** Only Minor findings are optional.
4. **Squash-merge, then pull `main`.** Don't leave local `main` stale after merging.
5. **Never force-push or amend commits already pushed to the PR branch** — push new commits instead.

