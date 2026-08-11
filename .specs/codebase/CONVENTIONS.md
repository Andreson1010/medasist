# Code Conventions

## Naming Conventions

**Files:** `snake_case.py`
Examples: `chunker.py`, `retriever.py`, `citations.py`, `store.py`

**Packages:** `snake_case`
Examples: `medasist`, `ingestion`, `vectorstore`, `generation`

**Classes:** `PascalCase`
Examples: `Settings`, `TextChunk`, `IngestionResult`, `UserProfile`, `_MultiStoreRetriever`

**Functions/Methods:** `snake_case`
Examples: `chunk_document`, `build_retriever`, `run_query`, `get_profile_config`

**Private helpers:** `_leading_underscore`
Examples: `_get_splitter` (`chunker.py:49`), `_format_context` (`chain.py:53`), `_validate_path` (`loader.py:74`), `_collection_name` (`store.py:89`)

**Constants:** `UPPER_SNAKE_CASE`
- Module-private: `_MIN_CHUNK_LENGTH` (`chunker.py:14`), `_SEPARATORS` (`chunker.py:16`), `_COLLECTION_ATTR` (`pipeline.py:20`)
- Public/cross-module: `PROMPT_TEMPLATES` (`profiles/schemas.py:31`), `PROFILE_LABELS` (`ui/app.py:27`)

**Type aliases:** `PascalCase`
Examples: `EmbedFn = Callable[[list[str]], list[list[float]]]` (`pipeline.py:18`)

**Singleton variables:** `_leading_underscore`
Examples: `_settings` (`config.py:149`), `_client` (`store.py:24`), `_registry` (`chain.py:24`)

## Code Organization

### Import Ordering (3 groups, blank lines between)

```python
from __future__ import annotations  # line 1, universal

# Group 1 — stdlib
import logging
from pathlib import Path

# Group 2 — third-party
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# Group 3 — local (absolute, never relative)
from medasist.config import Settings
from medasist.ingestion.schemas import DocType
```

- `import` statements before `from` imports within each group
- Local imports always absolute (`from medasist.config import ...`), never relative
- Lazy imports used to break circular deps: `pipeline.py:69` does `from langchain_openai import OpenAIEmbeddings` inside `build_embed_fn()`
- Router imports aliased: `from medasist.api.routers.ingest import router as ingest_router`

### File Structure (top-to-bottom)

1. `from __future__ import annotations` (line 1)
2. stdlib imports
3. third-party imports
4. local (`medasist.*`) imports
5. `logger = logging.getLogger(__name__)`
6. Module-level constants (`_MIN_CHUNK_LENGTH`, `_SEPARATORS`, type aliases)
7. Dataclasses / Pydantic models / Enums (data definitions first)
8. Private helper functions (`_`-prefixed)
9. Public functions (module API)
10. In `__init__.py`: `__all__ = [...]` re-exports

Section comments (banner-style `# ---` dividers) used in some files: `# Helpers privados`, `# API publica`, `# Constantes de UI`, `# Entry point`

### `__init__.py` Exports

| Package | `__all__` Exports |
|---------|-------------------|
| `ingestion/` | DocType, LoadedDocument, PageContent, TextChunk, ChunkMetadata, IngestionResult, load_pdf, chunk_document, build_metadata, build_metadata_batch, ingest_document, ingest_directory |
| `vectorstore/` | get_client, build_embeddings, get_vectorstore, get_all_vectorstores |
| `retrieval/` | build_retriever, retrieve |
| `profiles/` | PROMPT_TEMPLATES, ProfileConfig, UserProfile, get_profile_config |
| `generation/`, `api/`, `ui/`, `evaluation/` | (empty — no re-exports) |

`.flake8` has `per-file-ignores = __init__.py:F401` to allow re-exports without unused-import warnings.

## Type Safety / Documentation

### Type Hinting

Modern PEP 604 / PEP 585 syntax throughout (enabled by `from __future__ import annotations`):
- Unions: `Settings | None`, `str | None`, `EmbedFn | None`
- Generics: `list[TextChunk]`, `dict[DocType, Chroma]`
- `collections.abc` over `typing`: `from collections.abc import Callable` (NOT `typing.Callable`)
- `typing` used sparingly: `typing.Any`, `typing.Annotated` (FastAPI), `typing.AsyncGenerator`

### Three Modeling Strategies

1. **Pydantic `BaseModel`** — API boundary DTOs only (`api/schemas.py`): `QueryRequest`, `QueryResponse`, `IngestResponse`, `CitationResponse`
2. **Pydantic `BaseSettings`** — Configuration (`config.py`): `Settings` with `Field(default=..., gt=0, ge=0.0, le=2.0)` constraints
3. **`@dataclass(frozen=True)`** — Internal domain/value objects (ALL 10 dataclasses are frozen): `TextChunk`, `IngestionResult`, `GenerationResult`, `CitationItem`, `ProfileConfig`, `ChunkMetadata`, `LoadedDocument`, `PageContent`, `QueryResult`, `CitationResult`

### Docstrings — NumPy Style

All public AND private functions/classes use NumPy-style docstrings in **Portuguese (Brazilian)**.

Sections used: `Parameters`, `Returns`, `Raises`, `Attributes`, `Examples`

Example (`chunker.py:75`):
```python
def chunk_document(doc: LoadedDocument, settings: Settings) -> list[TextChunk]:
    """Divide um documento em chunks de texto por estrategia de DocType.

    Parameters
    ----------
    doc : LoadedDocument
        Documento carregado do disco.
    settings : Settings
        Configuracoes com tamanhos e overlaps por DocType.

    Returns
    -------
    list[TextChunk]
        Lista de chunks com metadados, excluindo textos curtos (< 50 chars).
    """
```

Inline code wrapped in double backticks: `` ``[N]`` ``, `` ``Settings`` ``, `` ``metadata["source"]`` ``

Private helpers (`_`-prefixed) also get full NumPy docstrings — stricter than typical.

## Error Handling

| Pattern | Where | Description |
|---------|-------|-------------|
| Catch broad `Exception`, log, return error result | `pipeline.py:128-139, 176-190` | Pipeline continues on per-file errors; never raises |
| Catch specific exception tuple for fallback chains | `loader.py:128-134` | `_PDFPLUMBER_ERRORS = (OSError, ValueError, KeyError, TypeError)` triggers PyMuPDF fallback |
| Catch, `logger.exception`, collect failures, continue | `retriever.py:137-139` | One failed store doesn't break query |
| Re-raise with `from exc` for context | `store.py:132-135` | Wraps in `RuntimeError` with context |
| Custom exception hierarchy | `ui/client.py:20-33` | `APIError` -> `RateLimitError` / `ServerError` / `RequestTimeoutError` |
| FastAPI `HTTPException` with named status codes | `ingest.py:51-53` | `status.HTTP_401_UNAUTHORIZED` |
| `secrets.compare_digest` for secret comparison | `ingest.py:49` | Timing-safe — never `==` |
| Validation raising `ValueError` / `FileNotFoundError` | `loader.py:89-92`, `profiles/schemas.py:117` | Documented in `Raises` docstring sections |

## Logging

- **Universal:** `logger = logging.getLogger(__name__)` — 16 modules, declared after imports
- **Zero `print()` calls** in all of `src/` (grep verified)
- **Log levels:** `debug` (per-chunk detail), `info` (milestones), `warning` (recoverable degradation), `error` (failures), `exception` (unexpected with traceback)
- **Lazy formatting:** All calls use `%s`-style placeholders (never f-strings in log args): `logger.info("Documento %s -> %d chunks", doc.path.name, len(chunks))`
- **Input truncation in logs:** Queries truncated — `question[:60]` (`chain.py:110`), `query[:50]` (`retriever.py:130`)

## Paths

- **100% `pathlib.Path`** — zero `import os` or `os.path` in `src/`
- `Path(path).resolve()`, `dir_path.glob("*.pdf")`, `path.mkdir(parents=True, exist_ok=True)`, `path.open("rb")`, `path.suffix.lower()`, `path.name`
- `str(path)` used where library API demands strings: `chromadb.PersistentClient(path=str(path))`

## Enums

Two core enums, both `str, Enum` mixins (serialize as JSON strings, work as dict keys):

```python
class DocType(str, Enum):
    BULA = "bula"
    DIRETRIZ = "diretriz"
    PROTOCOLO = "protocolo"
    MANUAL = "manual"

class UserProfile(str, Enum):
    MEDICO = "medico"
    ENFERMEIRO = "enfermeiro"
    ASSISTENTE = "assistente"
    PACIENTE = "paciente"
```

Enum values are designed as **suffixes of Settings field names**: `DocType.BULA` -> `chunk_size_bula`, `collection_bulas`; `UserProfile.MEDICO` -> `medico_temperature`. Basis of `getattr(settings, f"{key}_temperature")` pattern.

**Inconsistency:** UI layer (`ui/app.py:27-39`, `ui/client.py:122-127`) uses plain string keys instead of importing the enums — duplicates enum values as string literals.

## Configuration Access

| Pattern | Where | Usage |
|---------|-------|-------|
| Singleton `get_settings()` | Entry points: `main.py`, `ingest.py`, `app.py`, `chain.py:102` | Load cached Settings instance |
| Explicit `settings: Settings` param | Testable modules: `chunker.py`, `pipeline.py`, `retriever.py`, `store.py` | Inject for testing |
| Hybrid `settings: Settings \| None = None` | `chain.py:69`, `profiles/schemas.py:85` | Fallback to `get_settings()` if not provided |

Secrets use `SecretStr` and accessed via `.get_secret_value()`.

## Decorators

| Decorator | Purpose | Location |
|-----------|---------|----------|
| `@dataclass(frozen=True)` | Immutable value objects | 10 occurrences |
| `@asynccontextmanager` | FastAPI lifespan | `api/main.py:26` |
| `@app.get(...)` / `@router.post(...)` | FastAPI routes | `main.py:70`, `query.py:17`, `ingest.py:57` |
| `@limiter.limit("10/minute")` | slowapi rate limiting (stacked above `@router.post`) | `query.py:16`, `ingest.py:56` |
| `@classmethod` | `from_item` / `from_result` factory methods | `api/schemas.py:56,101` |
| `@property` | Computed `full_text` on `LoadedDocument` | `ingestion/schemas.py:58` |

## Async vs Sync

- **Core pipeline is entirely sync:** chunker, pipeline, retriever, chain, citations, store
- **Async confined to FastAPI HTTP layer:** `lifespan` (`@asynccontextmanager`), route handlers (`async def query/ingest`)
- **Async route calls sync chain directly** — no `await`, no `run_in_executor` (blocks event loop — see CONCERNS.md H4)
- **UI HTTP client is sync:** `httpx.Client` (not `AsyncClient`) — appropriate for Streamlit's sync model
- **Threading for thread-safety** (not async): `threading.Lock` in `store.py` and `prompts.py`

## Inconsistencies Found

1. **UI uses string keys instead of enums** — `ui/app.py` and `ui/client.py` duplicate enum values as string literals
2. **`pyproject.toml` has dead `[tool.flake8]` section** — flake8 doesn't read pyproject.toml; actual config lives in `.flake8`
3. **`typing.Any` used where stronger typing possible** — `chain.py:71,170` and `retriever.py:41` type `stores` as `dict[Any, Any]` instead of `dict[DocType, Chroma]`
4. **`DEFAULT_TIMEOUT` in `ui/client.py:12`** — no leading underscore despite being module-internal; duplicates `settings.ui_request_timeout`
