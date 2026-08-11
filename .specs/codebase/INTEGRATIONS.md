# External Integrations

## LLM / Embeddings

### LM Studio (Local LLM + Embedding Server)

**Service:** LM Studio — local, OpenAI-compatible API
**Purpose:** LLM inference (chat completions) and embedding generation
**Implementation:**
- LLM: `src/medasist/generation/chain.py:127-133` — `ChatOpenAI(base_url=..., api_key=..., model=...)`
- Embeddings (query): `src/medasist/vectorstore/store.py:68-86` — `OpenAIEmbeddings(base_url=..., api_key=..., model=..., check_embedding_ctx_length=False)`
- Embeddings (ingestion): `src/medasist/ingestion/pipeline.py:56-77` — `build_embed_fn()` creates `OpenAIEmbeddings` with identical params
**Configuration:**
- `LM_STUDIO_BASE_URL` (default: `http://localhost:1234/v1`; Docker: `http://host.docker.internal:1234/v1`)
- `LM_STUDIO_API_KEY` (default: `lm-studio`; SecretStr — LM Studio doesn't validate)
- `LM_STUDIO_LLM_MODEL` (default: `phi-3-mini`)
- `LM_STUDIO_EMBEDDING_MODEL` (default: `nomic-embed-text`)
**Authentication:** API key via `LM_STUDIO_API_KEY` (any string works)
**Key endpoints:** OpenAI-compatible `/v1/chat/completions`, `/v1/embeddings`
**Notes:** NOT OpenAI cloud — `langchain-openai` is used but `base_url` is pointed at LM Studio. Docker adds `extra_hosts: host.docker.internal:host-gateway` to reach host machine.

## Vector Database

### ChromaDB (Local Persistent)

**Service:** ChromaDB — local, file-based vector database
**Purpose:** Persistent vector store; one collection per DocType (bulas, diretrizes, protocolos, manuais) to avoid post-ANN contamination
**Implementation:**
- Client: `src/medasist/vectorstore/store.py:29-65` — `get_client()` singleton with thread-safe double-checked locking
- Collections: `src/medasist/vectorstore/store.py:96-137` — `get_vectorstore()` / `get_all_vectorstores()` open LangChain `Chroma` per DocType
- Write (ingestion): `src/medasist/ingestion/pipeline.py:176-180` — `collection.upsert(ids, embeddings, documents, metadatas)`
- Read (retrieval): `src/medasist/retrieval/retriever.py:84-170` — `similarity_search_with_score` with L2 distance threshold filtering
- Idempotency check: `src/medasist/ingestion/pipeline.py:84-86` — `collection.get(where={"sha256": ...})`
**Configuration:**
- `CHROMA_DIR` (default: `./chroma_db`; Path)
- Collection names: `collection_bulas`, `collection_diretrizes`, `collection_protocolos`, `collection_manuais` (Settings)
**Authentication:** None (local file-based)
**Docker:** Volume-mounted `./chroma_db:/app/chroma_db`

## PDF Processing

### pdfplumber (Primary)

**Service:** pdfplumber — Python PDF text extraction library
**Purpose:** Primary PDF text extraction engine
**Implementation:** `src/medasist/ingestion/loader.py:153-171` — `_extract_with_pdfplumber()` via `pdfplumber.open()` + `page.extract_text()`
**Configuration:** Pinned `pdfplumber==0.11.6`

### PyMuPDF / fitz (Fallback)

**Service:** PyMuPDF — Python PDF text extraction library
**Purpose:** Fallback when pdfplumber fails or returns insufficient text per page
**Implementation:**
- `src/medasist/ingestion/loader.py:174-202` — `_extract_with_pymupdf()` full-document fallback
- `src/medasist/ingestion/loader.py:205-230` — `_extract_page_with_pymupdf()` per-page fallback
**Configuration:** Pinned `PyMuPDF==1.25.5` (imported as `fitz`)
**Note:** CLAUDE.md mentions "OCR" but no OCR is implemented (no tesseract/pytesseract)

## API Middleware

### slowapi (Rate Limiting)

**Service:** slowapi — in-process rate limiting middleware
**Purpose:** IP-based rate limiting for API endpoints
**Implementation:**
- `src/medasist/api/deps.py:3-6` — `Limiter(key_func=get_remote_address)` singleton
- `src/medasist/api/main.py:63-64` — registers limiter and exception handler
- `src/medasist/api/routers/query.py:16` — `@limiter.limit("10/minute")`
- `src/medasist/api/routers/ingest.py:56` — `@limiter.limit("5/minute")`
**Configuration:** Limits hardcoded in decorators; key function: `get_remote_address` (client IP)

## HTTP Client (UI -> API)

### httpx (Internal HTTP)

**Service:** httpx — sync HTTP client
**Purpose:** Streamlit UI calls FastAPI backend
**Implementation:**
- `src/medasist/ui/client.py:93-118` — `check_health()` via `httpx.Client`
- `src/medasist/ui/client.py:121-200` — `query()` via `httpx.Client`
- `src/medasist/ui/app.py:266-272` — calls `query()` from Streamlit chat handler
**Configuration:**
- `API_BASE_URL` (default: `http://localhost:8000`; Docker: `http://api:8000`)
- `ui_request_timeout` (default: 120s; Settings)
- Health check timeout: hardcoded 5s
**Authentication:** None for `/query`; `X-Admin-Key` header for `/ingest` (not used by UI)
**Key endpoints:** `GET /health`, `POST /query`
**Notes:** Sync `httpx.Client` (not `AsyncClient`) — appropriate for Streamlit's sync execution model

## Admin Authentication

### Admin Key (X-Admin-Key header)

**Purpose:** Protects `/ingest` endpoint from unauthorized document uploads
**Implementation:** `src/medasist/api/routers/ingest.py:32-53` — `verify_admin_key()` dependency using `secrets.compare_digest()` (timing-safe)
**Configuration:** `ADMIN_API_KEY` env var (default: `dev-only`; SecretStr)
**Usage:** `X-Admin-Key` header on `POST /ingest` requests

## Container Orchestration

### Docker / Docker Compose

**Purpose:** Containerized deployment of API and UI services
**Implementation:**
- `docker-compose.yml` — production: api + ui services, `medasist_net` bridge, healthcheck-gated dependency, volumes
- `docker-compose.dev.yml` — dev: source code mounts + `--reload` flags
- `docker/api.Dockerfile` — multi-stage (builder + runtime), non-root `appuser`, healthcheck
- `docker/ui.Dockerfile` — single-stage, non-root `appuser`, healthcheck
**Services:**
- `api`: port 8000, volumes: `chroma_db`, `data/raw` (ro), `logs`; env: `LM_STUDIO_BASE_URL=http://host.docker.internal:1234/v1`
- `ui`: port 8501, depends on `api` (healthy); env: `API_BASE_URL=http://api:8000`
**External dependency:** LM Studio on host machine via `host.docker.internal:host-gateway`

## RAG Evaluation (Not Implemented)

### RAGAS + Datasets

**Purpose:** RAG pipeline quality evaluation (faithfulness, answer relevancy)
**Status:** Dependencies pinned (`ragas==0.2.15`, `datasets==3.6.0`) but **no code uses them**
- `src/medasist/evaluation/__init__.py` is empty
- `scripts/evaluate_rag.py` referenced in CLAUDE.md but **does not exist**

## Text Splitting

### langchain-text-splitters

**Purpose:** `RecursiveCharacterTextSplitter` for DocType-specific chunking
**Implementation:** `src/medasist/ingestion/chunker.py:7` — import and splitter construction with per-DocType separators
**Configuration:** **Not in any requirements file** — relies on transitive dependency from `langchain`

## Background Jobs

**Queue system:** None
**Jobs:** None — all processing is synchronous within request handlers or CLI scripts

## Webhooks

None
