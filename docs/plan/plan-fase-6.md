# Plano — Fase 6: API (MedAssist)

## Context

A Fase 6 expõe o pipeline RAG como API HTTP usando FastAPI. As fases anteriores entregaram toda a lógica de negócio (ingestion, vectorstore, retrieval, profiles, generation). Esta fase apenas conecta esses módulos a endpoints REST — sem nova lógica de domínio.

---

## Estrutura de Arquivos

```
src/medasist/api/
    __init__.py          (existe, vazio)
    deps.py              (NOVO — limiter singleton)
    schemas.py           (NOVO — DTOs request/response)
    main.py              (NOVO — app FastAPI + lifespan)
    routers/
        __init__.py      (existe, vazio)
        query.py         (NOVO — POST /query)
        ingest.py        (NOVO — POST /ingest)

tests/api/
    __init__.py          (existe, vazio)
    conftest.py          (NOVO — fixtures compartilhadas)
    test_health.py       (NOVO)
    test_query.py        (NOVO)
    test_ingest.py       (NOVO)
```

---

## Ordem de Implementação

1. `deps.py` — limiter singleton (sem deps internas)
2. `schemas.py` — DTOs Pydantic (sem deps internas)
3. `routers/query.py` — depende de schemas + generation
4. `routers/ingest.py` — depende de schemas + ingestion
5. `main.py` — depende de ambos os routers
6. `tests/api/conftest.py` — fixtures com mocks
7. `tests/api/test_health.py`
8. `tests/api/test_query.py`
9. `tests/api/test_ingest.py`

---

## Detalhamento por Arquivo

### `src/medasist/api/deps.py`
- `limiter = Limiter(key_func=get_remote_address)` — instância única compartilhada
- Importado em `main.py`, `query.py` e `ingest.py` para evitar circular imports

### `src/medasist/api/schemas.py`

```python
class QueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    profile: UserProfile
    doc_types: list[DocType] | None = None

class CitationResponse(BaseModel):
    index: int
    source: str
    section: str
    page: str

    @classmethod
    def from_item(cls, item: CitationItem) -> CitationResponse: ...

class QueryResponse(BaseModel):
    answer: str
    citations: list[CitationResponse]
    profile: UserProfile
    disclaimer: str
    is_cold_start: bool

    @classmethod
    def from_result(cls, result: GenerationResult) -> QueryResponse: ...

class IngestResponse(BaseModel):
    filename: str
    doc_type: DocType
    sha256: str
    chunks_indexed: int
    skipped: bool
    error: str | None
```

### `src/medasist/api/routers/query.py`

- `router = APIRouter()`
- `@router.post("/query", response_model=QueryResponse)`
- `@limiter.limit("10/minute")` (importado de `deps`)
- Handler: `request: Request` + `body: QueryRequest`
- Recupera chain: `request.app.state.chains[body.profile]`
- Chama `chain(body.question)` → `GenerationResult`
- Retorna `QueryResponse.from_result(result)`
- `doc_types` aceito mas não filtrado ainda (reservado — documentado no OpenAPI)

### `src/medasist/api/routers/ingest.py`

- Dependência `verify_admin_key`:
  ```python
  def verify_admin_key(x_admin_key: str = Header(...)) -> None:
      if not secrets.compare_digest(x_admin_key, settings.admin_api_key.get_secret_value()):
          raise HTTPException(status_code=401)
  ```
- `@router.post("/ingest", response_model=IngestResponse, dependencies=[Depends(verify_admin_key)])`
- `@limiter.limit("5/minute")`
- Salva upload em `Path(tempfile.mktemp(suffix=".pdf"))` + `try/finally` com `path.unlink(missing_ok=True)` (Windows-safe)
- Chama `ingest_document(path, doc_type, get_client(), settings)`

### `src/medasist/api/main.py`

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    stores = get_all_vectorstores(settings)
    app.state.chains = {
        profile: build_chain(stores, profile, settings)
        for profile in UserProfile
    }
    yield

app = FastAPI(lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.include_router(query_router)
app.include_router(ingest_router)

@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
```

---

## Testes

### `tests/api/conftest.py`

- Fixture `mock_chain`: `MagicMock()` retornando `GenerationResult` fake
- Fixture `client`: patch `medasist.api.main.get_all_vectorstores` e `medasist.api.main.build_chain`, depois `TestClient(app)`
- Fixture `admin_headers`: `{"X-Admin-Key": "test-admin-key"}`

### `test_health.py`
- `test_health_returns_200_ok`

### `test_query.py`

| Teste | Cenário |
|-------|---------|
| `test_happy_path` | Pergunta válida → 200, answer + citations |
| `test_disclaimer_always_present` | disclaimer nunca vazio |
| `test_cold_start_response` | `is_cold_start=True` propagado |
| `test_question_too_long` | 501 chars → 422 |
| `test_empty_question` | `""` → 422 |
| `test_invalid_profile` | perfil desconhecido → 422 |
| `test_rate_limit_exceeded` | 11 requests → 429 |

### `test_ingest.py`

| Teste | Cenário |
|-------|---------|
| `test_happy_path` | PDF válido + chave correta → 200 |
| `test_missing_admin_key` | sem header → 422 |
| `test_wrong_admin_key` | chave errada → 401 |
| `test_skipped_duplicate` | sha256 já indexado → `skipped=True` |
| `test_pipeline_error` | erro na ingestão → 500 |
| `test_rate_limit_exceeded` | 6 requests → 429 |

---

## Riscos e Mitigações

| Risco | Mitigação |
|-------|-----------|
| `TestClient` dispara lifespan real (chama ChromaDB/LLM) | Patch `get_all_vectorstores` + `build_chain` em `medasist.api.main` antes de `TestClient(app)` |
| Estado do rate limiter vaza entre testes | Usar instância fresh de `TestClient` por test class ou resetar storage do limiter |
| `NamedTemporaryFile` bloqueado no Windows | `Path(tempfile.mktemp(suffix=".pdf"))` + `path.unlink(missing_ok=True)` em `finally` |
| `secrets.compare_digest` com tipos diferentes | Garantir ambos os argumentos como `str` antes da comparação |
| `doc_types` silenciosamente ignorado | Documentar no schema OpenAPI como "reservado — filtragem futura" |

---

## Módulos a Reutilizar

| Módulo | Import |
|--------|--------|
| Config | `from medasist.config import Settings, get_settings` |
| Generation | `from medasist.generation.chain import GenerationResult, build_chain` |
| Citations | `from medasist.generation.citations import CitationItem` |
| Profiles | `from medasist.profiles.schemas import UserProfile` |
| Vectorstore | `from medasist.vectorstore.store import get_all_vectorstores` |
| DocType | `from medasist.ingestion.schemas import DocType` |

---

## Verificação

```bash
# Testes da Fase 6
pytest tests/api/ -v --cov=src/medasist/api --cov-fail-under=80

# Subir API localmente
uvicorn medasist.api.main:app --reload

# Smoke test manual
curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{"question": "qual a dose de amoxicilina?", "profile": "medico"}'
```
