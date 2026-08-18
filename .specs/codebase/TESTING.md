# Testing Infrastructure

## Test Frameworks

**Unit/Integration:** pytest 8.3.5 + pytest-cov 6.1.0 + pytest-asyncio 0.26.0 + pytest-mock 3.14.0
**E2E:** None (manual `make check` with curl probes against running services)
**Coverage:** coverage.py via pytest-cov (`--cov=src --cov-report=term-missing`)

## Test Organization

**Location:** `tests/` — mirrors `src/medasist/` package structure plus `tests/scripts/`
**Naming:** `test_*.py` for files; `test_*` functions and `Test*` classes (two coexisting styles)
**Structure:** 17 test files, 3 `conftest.py` files

```
tests/
├── conftest.py              # root: `settings` fixture
├── api/
│   ├── conftest.py          # mock_chain, cold_start_chain, client, admin_headers, ingest_client
│   ├── test_health.py
│   ├── test_ingest.py       # TestIngestAuth / TestIngestHappyPath / TestIngestSkipped / TestIngestError
│   └── test_query.py        # TestQueryHappyPath / TestQueryColdStart / TestQueryValidation
├── ingestion/               # test_chunker, test_loader, test_metadata, test_pipeline
├── vectorstore/             # test_store
├── retrieval/               # test_retriever
├── generation/              # test_chain, test_citations, test_prompts
├── profiles/                # test_schemas
├── ui/                      # conftest.py + test_client
├── scripts/                 # test_ingest_docs
└── evaluation/              # empty placeholder
```

## Testing Patterns

### Unit Tests

**Approach:** Pure pytest style — `assert` statements, `pytest.raises(..., match=...)`. No `unittest.TestCase`.
**Location:** All `test_*.py` files
**Mock patterns:**
- LLM: `patch("medasist.generation.chain.ChatOpenAI")` with `MagicMock` returning `AIMessage(content=...)`; `mock_llm_cls.assert_not_called()` verifies cold-start
- ChromaDB: `chromadb.PersistentClient(path=str(tmp_path / "chroma"))` — real ChromaDB in per-test temp dir (NOT `EphemeralClient` as AGENTS.md states)
- Embeddings: `_FakeEmbeddings(Embeddings)` returns deterministic 4-dim vectors; `_DivergentEmbeddings` for threshold testing
- PDF: `patch("medasist.ingestion.loader.pdfplumber")` and `patch("medasist.ingestion.loader.fitz")` with `MagicMock` factories
- HTTP: `patch("medasist.ui.client.httpx.Client")` with context-manager MagicMock

### Integration Tests

**Approach:** Real ChromaDB PersistentClient in `tmp_path` with fake embeddings
**Location:** `test_store.py`, `test_retriever.py`, `test_pipeline.py`
**Description:** Genuine vectorstore + retrieval + pipeline end-to-end (minus embedding model). `test_pipeline.py` covers idempotency, per-DocType collection routing, partial-failure resilience.

### API Integration Tests

**Approach:** FastAPI `TestClient` with lifespan mocked (ChromaDB/LLM patched out)
**Location:** `tests/api/`
**Description:** `conftest.py` patches `get_all_vectorstores` -> `{}` and `build_chain` -> mocked chain. HTTP-layer integration without external services. Covers 422 validation, cold start, all profiles, auth (401), happy path, skipped, error 500.

### E2E Tests

**Approach:** None automated. `make check` runs curl probes against running `:8000/health` and `:8501/_stcore/health` — manual smoke test.

## Test Execution

**Commands:**

| Purpose | Command |
|---------|---------|
| Full suite with 80% gate | `pytest tests/ -v --cov=src --cov-fail-under=80` |
| Full suite (no gate) | `pytest` (addopts in pyproject already injects `--cov=src --cov-report=term-missing`) |
| Single file | `pytest tests/ingestion/test_chunker.py -v` |
| Single test | `pytest tests/ingestion/test_chunker.py::test_chunk_bula_respects_sections -v` |
| Lint | `flake8 src/ tests/ scripts/` |
| Format | `black src/ tests/ scripts/` |

**Configuration:** `pyproject.toml` `[tool.pytest.ini_options]`:
- `testpaths = ["tests"]`
- `asyncio_mode = "auto"`
- `addopts = "--cov=src --cov-report=term-missing"`
- `pythonpath = ["src", "scripts"]`

## Coverage Targets

**Current:** Measured via `--cov=src` (source: `src/` only; `scripts/` not measured despite being tested)
**Goals:** 80% minimum
**Enforcement:** `--cov-fail-under=80` — **only in Makefile/AGENTS.md commands, NOT in `pyproject.toml` `addopts`**. A bare `pytest` will report coverage but won't fail below 80%.
**Omit:** `*/ui/*` excluded from coverage (Streamlit app)

## Test Coverage Matrix

| Code Layer | Required Test Type | Location Pattern | Run Command |
|------------|-------------------|------------------|-------------|
| `config.py` | none (indirect via fixtures) | — | `pytest` |
| `ingestion/loader.py` | unit | `tests/ingestion/test_loader.py` | `pytest tests/ingestion/test_loader.py` |
| `ingestion/chunker.py` | unit | `tests/ingestion/test_chunker.py` | `pytest tests/ingestion/test_chunker.py` |
| `ingestion/metadata.py` | unit | `tests/ingestion/test_metadata.py` | `pytest tests/ingestion/test_metadata.py` |
| `ingestion/pipeline.py` | integration | `tests/ingestion/test_pipeline.py` | `pytest tests/ingestion/test_pipeline.py` |
| `ingestion/schemas.py` | none (dataclass only) | — | `pytest` |
| `vectorstore/store.py` | integration | `tests/vectorstore/test_store.py` | `pytest tests/vectorstore/test_store.py` |
| `retrieval/retriever.py` | integration | `tests/retrieval/test_retriever.py` | `pytest tests/retrieval/test_retriever.py` |
| `generation/chain.py` | unit | `tests/generation/test_chain.py` | `pytest tests/generation/test_chain.py` |
| `generation/citations.py` | unit | `tests/generation/test_citations.py` | `pytest tests/generation/test_citations.py` |
| `generation/prompts.py` | unit | `tests/generation/test_prompts.py` | `pytest tests/generation/test_prompts.py` |
| `profiles/schemas.py` | unit | `tests/profiles/test_schemas.py` | `pytest tests/profiles/test_schemas.py` |
| `api/main.py` | integration (indirect) | `tests/api/test_health.py` | `pytest tests/api/` |
| `api/routers/query.py` | integration | `tests/api/test_query.py` | `pytest tests/api/test_query.py` |
| `api/routers/ingest.py` | integration | `tests/api/test_ingest.py` | `pytest tests/api/test_ingest.py` |
| `api/schemas.py` | none (indirect via API 422 tests) | — | `pytest tests/api/` |
| `api/deps.py` | none | — | — |
| `ui/client.py` | unit | `tests/ui/test_client.py` | `pytest tests/ui/test_client.py` |
| `ui/app.py` | none (coverage-excluded) | — | — |
| `evaluation/` | none (empty stub) | — | — |
| `scripts/ingest_docs.py` | unit | `tests/scripts/test_ingest_docs.py` | `pytest tests/scripts/test_ingest_docs.py` |

### Coverage Gaps

1. **`config.py`** — `get_settings()` singleton caching/mutation untested
2. **`api/schemas.py`** — validation constraints tested indirectly via 422 responses, not in isolation
3. **`api/deps.py`** — rate-limiting behavior (slowapi 429) entirely untested
4. **`ui/app.py`** — Streamlit rendering untested (intentionally excluded from coverage)
5. **`evaluation/`** — empty stub on both source and test sides

## Parallelism Assessment

| Test Type | Parallel-Safe? | Isolation Model | Evidence |
|-----------|----------------|-----------------|----------|
| Vectorstore/Retrieval/Pipeline | Yes | `tmp_path`-unique `PersistentClient` per test | `chromadb.PersistentClient(path=str(tmp_path / "chroma"))` in fixtures |
| API | Yes | Mocked lifespan, `TestClient` in `with` block | `conftest.py` patches `get_all_vectorstores` and `build_chain` |
| UI client | Yes | `httpx.Client` patched with MagicMock | `patch("medasist.ui.client.httpx.Client")` |
| Scripts | Yes | `mocker.patch("ingest_docs.ingest_directory")` | `test_ingest_docs.py` patches before invoking `main()` |

**No `pytest-xdist`** installed, but suite is parallel-safe by design. No shared mutable globals, no `tmp_path` collisions, no shared DB connections.

## Gate Check Commands

| Gate Level | When to Use | Command |
|------------|-------------|---------|
| Quick | After tasks with unit tests only | `pytest tests/ingestion/test_chunker.py -v` (or relevant module) |
| Full | After tasks with integration tests | `pytest tests/ -v --cov=src --cov-fail-under=80` |
| Build | After phase completion | `black src/ tests/ scripts/ && flake8 src/ tests/ scripts/ && pytest tests/ -v --cov=src --cov-fail-under=80` |

## Synthetic Data Approach

No `faker` / `factory-boy`. All synthetic data handcrafted with fictional medication names:
- Drugs: `Zolatril`, `Alphazol`, `Betazol`, `Gammacol`, `Alphazol X`
- Fake SHAs: `"deadbeef" * 8`, `"cafebabe" * 8`, `"feedface" * 8`
- Text generators: `_long_text(n_words=300)`, `_LONG_TEXT` (sentence repeated 30x)
- Factory functions: `_make_doc`, `_make_chunk`, `_make_pdfplumber_mock`, `_make_fitz_mock`, `_make_pdf_upload`, `_make_generation_result`

## Safety Rule Testing

The AGENTS.md "inegociavel" rules are explicitly tested:
- Cold start on empty retrieval: `test_chain.py::test_llm_not_called_on_cold_start` — `mock_llm_cls.assert_not_called()`
- Cold start on hallucinated citations: `test_chain.py` — LLM emits `[99]` with no matching CitationItem -> cold start fallback
- Disclaimer present: verified in all `GenerationResult` assertions
- Citation validation: `test_citations.py` — orphan removal, hallucinated markers stripped

