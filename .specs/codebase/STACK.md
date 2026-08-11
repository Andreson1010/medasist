# Tech Stack

**Analyzed:** 2026-08-07

## Core

- Language: Python 3.11+ (`requires-python = ">=3.11"`, `target-version = ["py311"]`)
- Runtime: CPython (Docker base: `python:3.11-slim`)
- Package manager: pip (split requirements files, no lock file)
- Build backend: setuptools>=68 + wheel (`src/` layout)
- Task runner: Makefile (targets: `up`, `down`, `dev`, `build`, `logs`, `ingest`, `test`, `lint`, `format`, `check`)
- CI/CD: None (`.github/` directory does not exist)

## LLM / RAG Orchestration

| Package | Version | Purpose |
|---------|---------|---------|
| langchain | 0.3.25 | Core LangChain framework (LCEL) |
| langchain-openai | 0.3.16 | `ChatOpenAI` + `OpenAIEmbeddings` — pointed at LM Studio via `base_url` |
| langchain-chroma | 0.2.4 | `Chroma` vectorstore wrapper for LangChain |
| langchain-community | 0.3.24 | Pinned but no direct import in source (possible transitive need) |
| langchain-text-splitters | (transitive) | `RecursiveCharacterTextSplitter` — NOT in requirements files, relies on langchain transitive dep |
| langchain-core | (transitive) | `Document`, `ChatPromptTemplate`, `StrOutputParser`, `BaseRetriever` — transitive |

## Vector Store

| Package | Version | Purpose |
|---------|---------|---------|
| chromadb | 1.0.9 | Local persistent vector store; `PersistentClient`, one collection per DocType |

## PDF Processing

| Package | Version | Purpose |
|---------|---------|---------|
| pdfplumber | 0.11.6 | Primary PDF text extraction (`loader.py`) |
| PyMuPDF | 1.25.5 | Fallback PDF extraction (imported as `fitz`); per-page and full-document fallback |

## Backend (API)

| Package | Version | Purpose |
|---------|---------|---------|
| fastapi | 0.115.9 | Web framework (routers, lifespan, `TestClient`) |
| uvicorn[standard] | 0.34.3 | ASGI server (includes uvloop, httptools) |
| python-multipart | 0.0.20 | Multipart form parsing for `UploadFile` in `/ingest` |
| slowapi | 0.1.9 | Rate limiting (`/query` = 10/min, `/ingest` = 5/min) |

## Frontend (UI)

| Package | Version | Purpose |
|---------|---------|---------|
| streamlit | 1.45.1 | Chat interface in `ui/app.py` |
| httpx | 0.28.1 | Sync HTTP client for UI->API calls (`ui/client.py`); also used in tests for `TestClient` |

## Config / Validation

| Package | Version | Purpose |
|---------|---------|---------|
| pydantic | 2.11.4 | `BaseModel` for API DTOs, `Field`, `SecretStr`, `ConfigDict` |
| pydantic-settings | 2.9.1 | `BaseSettings` for `config.py` (env-driven settings) |
| python-dotenv | 1.1.0 | `.env` file loading (used by pydantic-settings) |

## Logging

| Package | Version | Purpose |
|---------|---------|---------|
| python-json-logger | 3.3.0 | Pinned but **never imported** in source — no logging config wired in entry points |

## RAG Evaluation (Not Yet Implemented)

| Package | Version | Purpose |
|---------|---------|---------|
| ragas | 0.2.15 | RAG evaluation framework — `scripts/evaluate_rag.py` does not exist |
| datasets | 3.6.0 | HuggingFace datasets — dependency of RAGAS |

## Testing

| Package | Version | Purpose |
|---------|---------|---------|
| pytest | 8.3.5 | Test runner |
| pytest-cov | 6.1.0 | Coverage plugin (`--cov=src`, `--cov-report=term-missing`) |
| pytest-asyncio | 0.26.0 | Async test support (`asyncio_mode = "auto"`) |
| pytest-mock | 3.14.0 | `mocker` fixture (used only in `test_ingest_docs.py`) |

## Code Quality

| Package | Version | Purpose |
|---------|---------|---------|
| black | 24.10.0 | Formatter (`line-length = 88`, `target-version = ["py311"]`) |
| flake8 | 7.2.0 | Linter (`max-line-length = 88`, `extend-ignore = E203, W503`) |
| flake8-bugbear | 24.12.12 | Additional bug pattern checks |

## Docker Setup

| Service | Dockerfile | Port | Key Details |
|---------|-----------|------|-------------|
| api | `docker/api.Dockerfile` (multi-stage) | 8000 | `requirements-api.txt`, non-root `appuser`, healthcheck `/health` |
| ui | `docker/ui.Dockerfile` (single-stage) | 8501 | `requirements-ui.txt`, non-root `appuser`, healthcheck `/_stcore/health` |

- Network: `medasist_net` (bridge)
- Volumes: `chroma_db`, `data/raw` (read-only), `logs`
- LM Studio access: `http://host.docker.internal:1234/v1` (host machine)
- Dev override: source mounts + `--reload` flags

## LLM Configuration

| Aspect | Value |
|--------|-------|
| Provider | LM Studio (local, OpenAI-compatible API) — NOT OpenAI cloud |
| LLM model | `phi-3-mini` (default, configurable via `LM_STUDIO_LLM_MODEL`) |
| Embedding model | `nomic-embed-text` (default, configurable via `LM_STUDIO_EMBEDDING_MODEL`) |
| URL (local) | `http://localhost:1234/v1` |
| URL (Docker) | `http://host.docker.internal:1234/v1` |
| LLM client | `langchain_openai.ChatOpenAI` with `base_url` override |
| Embedding client | `langchain_openai.OpenAIEmbeddings` with `check_embedding_ctx_length=False` |
| Chain pattern | LCEL: `prompt | ChatOpenAI | StrOutputParser()` |

## Requirements File Strategy

| File | Purpose | Used By |
|------|---------|---------|
| `requirements.txt` | Full install (all categories) | Local dev |
| `requirements-api.txt` | API container only (no UI, no RAGAS) | `docker/api.Dockerfile` |
| `requirements-ui.txt` | UI container only (streamlit + httpx) | `docker/ui.Dockerfile` |
| `requirements-dev.txt` | Test + lint tooling | Local dev only |

## Notable Gaps

1. **No lock file** — all direct deps pinned with `==`, but transitive deps (langchain-core, langchain-text-splitters) are not pinned
2. **`langchain-text-splitters`** not in any requirements file — relies on transitive dependency
3. **`python-json-logger`** pinned but never imported — no logging config in entry points
4. **`ragas` + `datasets`** pinned but `scripts/evaluate_rag.py` does not exist
5. **No CI/CD pipeline** — quality gates are manual via Makefile
