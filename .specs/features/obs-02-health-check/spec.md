# Spec — OBS-02: Health check de dependências

**Slug:** `obs-02-health-check`
**Escopo TLC:** medium
**Story aprovada:** Checkpoint 1 (2026-08-11).
**Decisões das perguntas em aberto (aprovadas):**
1. HTTP **sempre 200**; degradação sinalizada via `status` top-level `"degraded"` (preserva `curl -f` do docker-compose, `test_health`, `check_health`).
2. Nova setting `healthcheck_timeout` com default **3s**, aplicada ao probe do LM Studio (único com timeout próprio; o cliente ChromaDB local é in-process, sem bound).
3. Probe do LM Studio: **`GET {base_url}/models`** (liveness barato); embedding fica para diagnóstico manual.
4. Expor `latency_ms` por dependência no `details`.
5. Semântica: `unavailable` = inacessível; `degraded` = parcialmente funcional (ex: coleções ausentes); top-level apenas `ok`/`degraded`.
6. Probe ChromaDB: `heartbeat()` + `list_collections()` (coleções ausentes → `degraded`).
7. `chroma_dir` read-only fora do probe (delegado ao log da ingestão).

---

## Contexto

`GET /health` hoje (`src/medasist/api/main.py:93-102`) retorna `{"status": "ok"}` fixo — não verifica nenhuma dependência. O lifespan já constrói `get_client(settings)` (ChromaDB singleton thread-safe) e `build_embeddings(settings)` (LM Studio). Não existe `HealthResponse` em `api/schemas.py`. Não há timeout de probe configurado. UI (`check_health` em `ui/client.py:114`) e docker healthcheck (`curl -f`, `docker-compose.yml:18-23`) dependem de HTTP 200 + `status`.

## Objetivo

`GET /health` deve verificar ChromaDB (`heartbeat()` + `list_collections()`) e LM Studio (`GET {base_url}/models`), retornar status por dependência com latência e detalhes, em modelo Pydantic validado — sem quebrar UI, docker healthcheck nem o teste existente `test_health.py`.

## Arquitetura da solução

Novo módulo `src/medasist/api/health.py` com a lógica de probes, desacoplada do route handler. O handler em `main.py` chama `check_dependencies(settings)` e monta a resposta.

### Modelos Pydantic (`api/schemas.py`)

```python
class DependencyStatus(str, Enum):
    OK = "ok"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"

class DependencyHealth(BaseModel):
    status: DependencyStatus
    details: str
    latency_ms: int

class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    chromadb: DependencyHealth
    lm_studio: DependencyHealth
```

- `status` top-level: `"ok"` se todas as deps `ok`; senão `"degraded"`. Sem terceiro valor no top-level.
- `latency_ms`: tempo do probe em ms (int).

### `src/medasist/api/health.py`

```python
def check_chromadb(settings) -> DependencyHealth
def check_lm_studio(settings, timeout) -> DependencyHealth
def check_dependencies(settings) -> HealthResponse
```

- **`check_chromadb`**: `client = get_client(settings)`; mede `time.perf_counter()`; `client.heartbeat()` (liveness) → `ok`; se ok, `client.list_collections()` e compara nomes com as 4 coleções esperadas (`settings.collection_*`) → se faltar coleção, `degraded` com detalhe das ausentes; qualquer exceção → `unavailable` com mensagem do erro (via `str(exc)`, truncada). Log de falha com `logger` + `%s`. O singleton `get_client` não é "envenenado": heartbeat não muta estado.
- **`check_lm_studio`**: `httpx.get(f"{settings.lm_studio_base_url}/models", timeout=timeout)` → 2xx = `ok`; timeout = `unavailable` (details "timeout"); outra exceção de conexão = `unavailable`; status não-2xx = `unavailable` (details com código HTTP). Log de falha com `%s`.
- **`check_dependencies`**: roda os dois probes (sequencial), soma `latency_ms` de cada, e monta `HealthResponse` com `status = "ok" if both ok else "degraded"`.
- Timeout: `settings.healthcheck_timeout` (float, `gt=0`, default `3.0`) limita o probe do LM Studio; o cliente ChromaDB persistente local (in-process) não expõe timeout próprio em `heartbeat`/`list_collections` — tradeoff aceito para dev local.
- `from __future__ import annotations`, `pathlib` desnecessário, `logging`, `time`, `httpx`, `Any`.

### Route handler (`api/main.py`)

- Substituir o corpo de `health()`:
```python
@app.get("/health", summary="Health check", response_model=HealthResponse)
def health() -> HealthResponse:
    settings = get_settings()
    return check_dependencies(settings)
```
- Preserva `status_code=200` sempre (default FastAPI).

## Arquivos que serão alterados

| Arquivo | Mudança |
|---|---|
| `src/medasist/config.py` | Nova setting `healthcheck_timeout: float = Field(default=3.0, gt=0)`. |
| `src/medasist/api/health.py` | **Novo** — `check_chromadb`, `check_lm_studio`, `check_dependencies`. |
| `src/medasist/api/schemas.py` | Novos modelos `DependencyStatus`, `DependencyHealth`, `HealthResponse`. |
| `src/medasist/api/main.py` | Handler `/health` chama `check_dependencies`; `response_model=HealthResponse`. |
| `.env.example` | Documentar `HEALTHCHECK_TIMEOUT=3.0`. |

## Mudanças de modelo de dados

- **API (wire shape do /health muda de `{"status":"ok"}` para o objeto `HealthResponse`)** — quebra contrato do teste `test_health.py:10` (assert exact dict) e do docker healthcheck? Não: o teste será atualizado, e o healthcheck só checa HTTP 200. UI continua compatível (`status` preservado em `"ok"`/`"degraded"`). **Nenhuma mudança de banco.**
- `Settings.healthcheck_timeout` novo (default 3.0, validado `gt=0`).

## Detalhe dos probes

### ChromaDB
- Liveness: `client.heartbeat()` → int (ns); exceção → `unavailable`.
- Riqueza: `client.list_collections()` → nomes; `expected = {settings.collection_bulas, ..., collection_manuais}`; se `expected - atual` não vazio → `degraded` (details: "coleções ausentes: X, Y"); senão `ok`.
- Latência: ms do heartbeat + list_collections.
- Timeout: o cliente persistente local (in-process) não expõe timeout próprio — `heartbeat`/`list_collections` não são limitados por `healthcheck_timeout`. Tradeoff aceito para dev local (apenas o probe do LM Studio é limitado).

### LM Studio
- `httpx.get(url + "/models", timeout=healthcheck_timeout)`; base_url já termina em `/v1` → `/v1/models` (compatível OpenAI).
- 200 → `ok`; `httpx.TimeoutException` → `unavailable` ("timeout após Xs"); `httpx.HTTPError`/`ConnectError` → `unavailable` (erro); status ≥300 → `unavailable` (details `HTTP <code>`).
- Latência: ms do GET.

## Requisitos (IDs traçam aos CAs da story)

| ID | AC | Requisito |
|---|---|---|
| REQ-1 | CA-01 | Deps saudáveis → HTTP 200, `status:"ok"`, cada dep `ok`. |
| REQ-2 | CA-02 | Modelo Pydantic `HealthResponse` com `DependencyHealth` por dep (`chromadb`, `lm_studio`) com `status`+`details`+`latency_ms`; `response_model=`. |
| REQ-3 | CA-03 | LM Studio fora → HTTP 200 + `status:"degraded"` + `lm_studio.status:"unavailable"`; `chromadb` mantém `ok`. |
| REQ-4 | CA-04 | `heartbeat()` falha → `chromadb.status:"unavailable"`, erro logado (`%s`, sem traceback ao cliente). |
| REQ-5 | CA-05 | `GET /models` timeout/não-2xx → `lm_studio.status:"unavailable"` com detalhe (timeout/código). |
| REQ-6 | CA-06 | Novo `Settings.healthcheck_timeout` (float, `gt=0`, default 3.0) aplicado ao probe do LM Studio (único com timeout próprio; ChromaDB local in-process sem bound — tradeoff documentado em "Detalhe dos probes"). |
| REQ-7 | CA-07 | `status` top-level restrito a `ok`/`degraded`; UI `check_health` inalterada. |
| REQ-8 | Edge | `list_collections()` ausente/falha → `chromadb.status:"degraded"` (alive, coleções ausentes). |
| REQ-9 | Edge | Timeout: probe lança `TimeoutException` → `unavailable` com detalhe "timeout". |
| REQ-10 | Edge | Falha num probe não envenena o outro nem o singleton `get_client`. |
| REQ-11 | Edge | Latência por dep no `details` (`latency_ms`). |
| REQ-12 | Segurança | Nenhum dado de paciente; apenas mensagens de erro/latência; `%s` lazy. |

## Riscos

| Risco | Nível | Mitigação |
|---|---|---|
| Quebra do `test_health.py` existente (exact dict) | Baixo | Atualizar o teste para o novo shape; healthcheck docker/UI só checam status/200. |
| `/health` travar por dep travada | Médio | Apenas o probe do LM Studio é limitado por `healthcheck_timeout` (3s); o cliente ChromaDB persistente local roda in-process e `heartbeat`/`list_collections` não têm bound — tradeoff aceito para dev local (endpoint nunca excede ~3s + tempo do probe ChromaDB local). |
| Singleton `get_client` compartilhado | Baixo | Probes usam o client existente sem mutar estado; exceção não persiste. |
| Dependência de `httpx` no health module | Baixo | `httpx` já é dependência (usada em `ui/client.py`). |
| `GET /models` requer LM Studio com rota compatível | Baixo | LM Studio expõe `/v1/models` (padrão OpenAI); documentado no `.env.example`. |

## Perguntas em aberto

Nenhuma (as 7 da story foram decididas e aprovadas no Checkpoint 1).

---

**APROVAÇÃO:** conforme decisão do Checkpoint 2.
