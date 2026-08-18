---
name: build-with-tests
description: How this team builds — match existing patterns, write tests alongside every function, run the full test suite and coverage check before declaring done. Use this skill whenever writing new code, fixing bugs, or extending existing modules in this project. Triggers on any implementation task.
---

# Build With Tests

How we build in this project. Not a methodology — just the rules that stop us from breaking things.

## The pattern before the code

Before writing anything, find the existing pattern for what you're about to do:

- New ingestion step → read `src/medasist/ingestion/pipeline.py`, match how existing stages are structured
- New config value → add it to `src/medasist/config.py`, never hardcode it inline
- New data access → follow the pattern in `src/medasist/vectorstore/store.py`
- New retrieval logic → follow the pattern in `src/medasist/retrieval/retriever.py`
- New generation/prompt logic → follow the pattern in `src/medasist/generation/chain.py`
- New constant → `config.py` is the single source of truth

If you can't find an existing pattern, ask. Don't invent a new one silently.

## Code rules (non-negotiable)

- **Never mutate.** Return new objects and copies. Never modify in place. All dataclasses are `frozen=True`.
- **Functions under 50 lines.** If it's longer, it's doing too much.
- **Nesting under 4 levels.** Flatten with early returns.
- **No hardcoded values.** Everything goes in `config.py`.
- **No silent failures.** Every exception is caught and logged with context, or re-raised explicitly.
- **Validate at boundaries.** External input (PDFs, API requests, user queries) gets validated on entry. Internal function calls don't need defensive checks.
- **`from __future__ import annotations`** on line 1 of every `.py` file.
- **`pathlib.Path`** always, never raw strings for paths.
- **`logger = logging.getLogger(__name__)`** never `print()`.
- **Lazy log formatting** — `%s` placeholders, never f-strings in log args.
- **NumPy-style docstrings** in Portuguese on all public and private functions.

## RAG safety rules (inegociáveis)

These are the medical safety rules from CLAUDE.md — they must be enforced in code and tests:

1. **Disclaimer obrigatório** — every `GenerationResult` includes `settings.disclaimer`
2. **Cold start** — empty retrieval → fixed message, LLM never called (zero cost, zero hallucination)
3. **Citation obrigatória** — every response cites at least one source `[N]`; orphan markers are stripped
4. **No patient data** — code, tests, and logs use only synthetic data (fictional drug names like `Zolatril`, `Alphazol`)

## Writing tests alongside code

Write the test before or immediately after the function — not at the end of the task.

### Test naming
```
test_<description_of_what_it_tests>
```
Example: `test_chunk_bula_respects_sections`, `test_cold_start_returns_fixed_message`

Two coexisting styles in this project: module-level `test_*` functions and `Test*` classes grouping related cases.

### What every test must cover
1. Happy path — normal input, expected output
2. Empty/zero edge case — empty list, empty string, zero results
3. Failure path — what happens when something goes wrong

### LLM calls — always mock
```python
# Never make real calls in tests. Always patch the LLM client.
with patch("medasist.generation.chain.ChatOpenAI") as mock_llm_cls:
    mock_llm_instance = MagicMock()
    mock_llm_instance.return_value = AIMessage(content="mocked response [1]")
    mock_llm_cls.return_value = mock_llm_instance
    ...
```

### ChromaDB — use tmp_path PersistentClient
```python
# Tests use real ChromaDB in a per-test temp directory (NOT EphemeralClient)
@pytest.fixture
def client(tmp_path):
    return chromadb.PersistentClient(path=str(tmp_path / "chroma"))
```

### Empty retrieval — always test
Every function that retrieves documents must have a test for when retrieval returns zero results. This is not optional — it's the cold start safety rule.

### Citation validation — always test
Every response path must be tested for orphan citation removal and hallucinated marker detection.

## Gate checks — run before declaring done

In order. Do not skip. Do not declare done if any fail.

```bash
# Full test suite with coverage
pytest tests/ -v --cov=src --cov-fail-under=80

# Format
black src/ tests/ scripts/

# Lint
ruff check src/ tests/ scripts/

# Lint with auto-fix (imports, unused vars)
ruff check --fix src/ tests/ scripts/
```

Coverage must stay at or above **80%**. If it drops, write more tests — not fewer assertions.

```bash
# Single file (faster feedback during development)
pytest tests/ingestion/test_chunker.py -v

# Single test
pytest tests/ingestion/test_chunker.py::test_chunk_bula_respects_sections -v
```

## What "done" means

- All gate checks pass
- Coverage ≥ 80%
- No hardcoded values introduced
- No new patterns invented without documenting why
- Every new function has at least: happy path test, empty edge case test, failure test
- Medical safety rules enforced (disclaimer, cold start, citations, no patient data)
- `ruff check src/ tests/ scripts/` passes
- `black --check src/ tests/ scripts/` passes
