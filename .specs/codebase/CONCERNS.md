# Concerns

## HIGH Severity

### H1. Runtime Bug: `get_client()` called without required argument

- **File:** `src/medasist/api/routers/ingest.py:96`
- **Evidence:** `get_client()` called with no arguments, but signature is `def get_client(settings: Settings) -> chromadb.PersistentClient` (`store.py:29`). The lifespan in `main.py:40` calls it correctly as `get_client(settings)`.
- **Impact:** `TypeError` at runtime whenever `/ingest` endpoint is called with valid admin key
- **Why not caught:** All tests mock `get_client` via `patch("medasist.api.routers.ingest.get_client")`, so the real function is never invoked
- **Fix:** Change to `get_client(settings)` — `settings` is already available via `get_settings()` call at `ingest.py:85`

### H2. Weak default admin API key

- **File:** `src/medasist/config.py:88`
- **Evidence:** `admin_api_key: SecretStr = Field(default=SecretStr("dev-only"))`. `.env` has `ADMIN_API_KEY=troque-por-chave-segura` (placeholder).
- **Impact:** If deployed without setting `ADMIN_API_KEY`, the ingest endpoint is protected only by `"dev-only"` or a placeholder string. No validation enforces minimum key length.
- **Fix:** Add `min_length` validation or require the env var to be set (no default) in production

### H3. `.env` file exists with placeholder secrets

- **File:** `.env:19`
- **Evidence:** `.env` present with `ADMIN_API_KEY=troque-por-chave-segura`. `.env` is in `.gitignore` (correct), but the file exists locally.
- **Impact:** If not changed before deployment, admin endpoint is effectively open
- **Fix:** Ensure deployment process requires explicit `ADMIN_API_KEY` setting; consider startup validation that rejects placeholder values

### H4. Synchronous LLM call blocks the async event loop

- **File:** `src/medasist/api/routers/query.py:26-44`
- **Evidence:** `async def query(...)` calls `chain(body.question)` synchronously. `run_query()` -> `chain.invoke()` makes a blocking HTTP call to LM Studio (potentially 10-60+ seconds).
- **Impact:** No other request can be served while LLM generates a response. Single-request concurrency.
- **Fix:** Either make the endpoint `def` (FastAPI runs sync endpoints in threadpool) or use `await` with async chain invocation (`ainvoke`)

### H5. No file size limit on `/ingest` upload

- **File:** `src/medasist/api/routers/ingest.py:90-91`
- **Evidence:** `content = await file.read()` reads entire uploaded PDF into memory. No size limit check. FastAPI/Starlette imposes no default upload size limit.
- **Impact:** Malicious actor with admin key could upload very large file and exhaust server memory
- **Fix:** Check `file.size` or stream upload in chunks; add configurable `max_upload_size` setting

### H6. `doc_types` filtering silently ignored

- **File:** `src/medasist/api/schemas.py:27-33` (field definition), `src/medasist/api/routers/query.py:43-44` (not used)
- **Evidence:** `QueryRequest` accepts `doc_types: list[DocType] | None` and UI sends it from sidebar multiselect (`app.py:269`). But query router never reads `body.doc_types` — calls `chain(body.question)` which searches **all** stores.
- **Impact:** Users who filter by "Bula only" get results from all document types. The "one collection per DocType to avoid post-ANN contamination" architecture is bypassed at query time.
- **Fix:** Pass `body.doc_types` to `run_query()` -> `build_retriever()` -> filter which stores to search

## MEDIUM Severity

### M1. No CORS middleware configured

- **File:** `src/medasist/api/main.py` (entire file)
- **Evidence:** No `CORSMiddleware` added. UI communicates server-side via httpx (works), but browser-based clients would be blocked by CORS.
- **Fix:** Add `CORSMiddleware` if browser-based integrations are planned

### M2. CLAUDE.md documentation mismatches implementation

- **File:** `CLAUDE.md`
- **Evidence:**
  - Says "OpenAI GPT-4o" — actual is LM Studio with `phi-3-mini`
  - Says "MMR" for retrieval — actual uses plain `similarity_search_with_score` with L2 distance threshold (no MMR; grep for `mmr`/`MMR` returns zero)
  - Says "pdfplumber + fallback PyMuPDF/OCR" — **no OCR implemented** (grep for `ocr`/`tesseract`/`pytesseract` returns zero)
  - Says `python scripts/evaluate_rag.py` — **script does not exist**
- **Fix:** Update CLAUDE.md to match actual implementation

### M3. `section` and `page` metadata never populated in citations

- **Files:** `src/medasist/ingestion/pipeline.py:165-174`, `src/medasist/generation/citations.py:53-59`
- **Evidence:** Pipeline metadata dict includes `doc_type`, `source_path`, `sha256`, `chunk_index`, `char_count` — **not** `section` or `page`. `build_citations()` reads `meta.get("section", "")` and `meta.get("page", "")` which always return `""`. Documented in `todo/TODO.md`.
- **Impact:** Violates security rule #3: `"Toda resposta deve citar ao menos uma fonte: [N] <nome_doc> — Secao: <secao>, Pag. <pag>"` — section and page are always blank
- **Root cause:** `chunk_document()` uses `doc.full_text` which concatenates all pages, losing page number info. `PageContent.page_number` is available but never propagated to `TextChunk`.
- **Fix:** Propagate page numbers through chunking; extract section headings from document structure

### M4. Code duplication: `_COLLECTION_ATTR` dict

- **Files:** `src/medasist/vectorstore/store.py:17-22`, `src/medasist/ingestion/pipeline.py:20-25`
- **Evidence:** `_COLLECTION_ATTR: dict[DocType, str]` defined identically in two modules
- **Fix:** Define in one place (e.g., `config.py` or `ingestion/schemas.py`) and import

### M5. Code duplication: `OpenAIEmbeddings` construction

- **Files:** `src/medasist/vectorstore/store.py:68-86`, `src/medasist/ingestion/pipeline.py:56-77`
- **Evidence:** Both construct `OpenAIEmbeddings` with identical parameters (`base_url`, `api_key`, `model`, `check_embedding_ctx_length=False`)
- **Fix:** Unify — `pipeline.py` should use `build_embeddings()` from `store.py`

### M6. `langchain-text-splitters` not in requirements files

- **File:** `requirements.txt`, `requirements-api.txt`
- **Evidence:** `chunker.py:7` imports `from langchain_text_splitters import RecursiveCharacterTextSplitter` but no requirements file lists it. Relies on transitive dependency from `langchain==0.3.25`.
- **Fix:** Add `langchain-text-splitters` to requirements explicitly

### M7. Broad `except Exception` in retriever swallows programming errors

- **File:** `src/medasist/retrieval/retriever.py:137`
- **Evidence:** `except Exception:` catches all exceptions from `similarity_search_with_score`, logs via `logger.exception()`, and continues. Provides resilience but also swallows `TypeError`, `AttributeError` that should surface in development.
- **Fix:** Catch specific ChromaDB exceptions; let programming errors propagate

### M8. No logging configuration in application entry points

- **Files:** `src/medasist/api/main.py`, `src/medasist/ui/app.py`
- **Evidence:** `python-json-logger==3.3.0` pinned but never imported. No `dictConfig`/`basicConfig` in entry points (only in `scripts/ingest_docs.py:71`). `LOG_LEVEL` and `LOG_DIR` settings defined but never used to configure logging. All loggers use root logger defaults (WARNING level, basic format).
- **Fix:** Add logging configuration in API/UI entry points; wire `LOG_LEVEL` and structured JSON logging

## LOW Severity

### L1. Test coverage gaps

- `config.py` — no dedicated test (singleton behavior untested)
- `api/deps.py` — rate-limiting (slowapi 429) entirely untested
- `api/schemas.py` — validation tested indirectly via 422, not in isolation
- `ui/app.py` — Streamlit rendering untested (intentionally excluded)
- `evaluation/` — empty stub on both sides

### L2. Makefile uses Linux-only `md5sum` command

- **File:** `Makefile:19,22,25`
- **Evidence:** `dev` and `.req-hash` targets use `md5sum`, not available on Windows (project platform is `win32`)
- **Fix:** Use `Get-FileHash` (PowerShell) or cross-platform alternative

### L3. Broad `except Exception` in health check and loader fallbacks

- **Files:** `ui/client.py:116`, `loader.py:194,226`, `store.py:132`, `pipeline.py:130,181`
- **Evidence:** Broad catches with logging — reasonable for PDF processing and resilience, but could hide programming errors. Mitigated by `logger.exception` / `logger.warning`.

### L4. Settings singleton never reset — potential test isolation issue

- **File:** `src/medasist/config.py:149-163`
- **Evidence:** `get_settings()` caches global `_settings` that is never cleared. Code paths calling `get_settings()` without injection (`ui/client.py:108,159`) could leak state between tests.
- **Fix:** Add `reset_settings()` for test cleanup or use `@pytest.fixture(autouse=True)` to reset

### L5. `scripts/ingest_docs.py` creates its own `PersistentClient` bypassing singleton

- **File:** `scripts/ingest_docs.py:92`
- **Evidence:** `chromadb.PersistentClient(path=str(settings.chroma_dir))` instead of `get_client(settings)` — bypasses singleton logic and thread-safety. Not a problem for single-threaded CLI, but inconsistent.

### L6. No retry logic for LM Studio calls

- **Files:** `chain.py:136`, `store.py:81`, `pipeline.py:177`
- **Evidence:** No retry/backoff. Transient errors (model loading, GC pause) cause user-facing failures.

### L7. `generation/__init__.py` and `api/__init__.py` are empty

- **Evidence:** Unlike `ingestion`, `vectorstore`, `retrieval`, `profiles`, these define no `__all__` and export nothing. Consumers must use full module paths. Inconsistent with rest of codebase.

### L8. No input sanitization for prompt injection

- **File:** `src/medasist/api/schemas.py:25`
- **Evidence:** `QueryRequest.question` validates `min_length=1, max_length=500` only. User question is directly interpolated into prompt template. No system message separation or output validation beyond citation checking.

### L9. UI Dockerfile not multi-stage

- **File:** `docker/ui.Dockerfile`
- **Evidence:** Single-stage build (unlike `api.Dockerfile` which uses builder + runtime). Minor image size concern.

### L10. `typing.Any` used where stronger typing possible

- **Files:** `chain.py:71,170`, `retriever.py:41`
- **Evidence:** `stores` typed as `dict[Any, Any]` instead of `dict[DocType, Chroma]` — weakens type contract

### L11. `DEFAULT_TIMEOUT` in `ui/client.py:12` breaks convention

- **Evidence:** No leading underscore despite being module-internal; duplicates `settings.ui_request_timeout` (default 120.0)

### L12. Dead `[tool.flake8]` section in `pyproject.toml`

- **File:** `pyproject.toml:18-21`
- **Evidence:** flake8 does not read `pyproject.toml` — actual config lives in `.flake8` with identical content. Dead config, harmless but misleading.

## Dependency Risks

| Package | Risk | Evidence |
|---------|------|----------|
| `langchain-text-splitters` | Not pinned in requirements | Relies on transitive dep from `langchain==0.3.25` |
| `python-json-logger` | Pinned but unused | No logging config wired in entry points |
| `ragas` + `datasets` | Pinned but unused | `evaluate_rag.py` does not exist |
| `langchain-community` | Pinned but no direct import | Possible transitive runtime need |
| No lock file | Transitive deps not pinned | `langchain-core`, `langchain-text-splitters` versions could drift |

## Missing Features (Referenced but Not Implemented)

| Feature | Referenced In | Status |
|---------|--------------|--------|
| `scripts/evaluate_rag.py` | CLAUDE.md, README.md | File does not exist |
| RAGAS evaluation | `evaluation/` package, requirements.txt | Empty `__init__.py` |
| `doc_types` query filtering | API schema, UI sidebar | Accepted but silently ignored |
| OCR fallback | CLAUDE.md | No tesseract/pytesseract |
| MMR retrieval | CLAUDE.md | Uses plain `similarity_search_with_score` |
| Section/page in citations | Security rule #3, `citations.py` | Always empty strings |
| Structured JSON logging | `python-json-logger` in requirements | No logging config in entry points |
| CI/CD pipeline | CLAUDE.md mentions code review | No `.github/` directory |

## Summary

| Severity | Count | Key items |
|----------|-------|-----------|
| **HIGH** | 6 | `get_client()` bug (H1), weak admin key (H2), `.env` placeholder (H3), sync-in-async blocking (H4), no upload size limit (H5), `doc_types` ignored (H6) |
| **MEDIUM** | 8 | No CORS (M1), doc mismatches (M2), empty citations (M3), code dup (M4, M5), missing dep pin (M6), broad except (M7), no logging config (M8) |
| **LOW** | 12 | Test gaps (L1), Windows Makefile (L2), broad excepts (L3), singleton leakage (L4), inconsistent client (L5), no retries (L6), empty inits (L7), no prompt injection mitigation (L8), Dockerfile (L9), weak typing (L10), convention break (L11), dead config (L12) |
