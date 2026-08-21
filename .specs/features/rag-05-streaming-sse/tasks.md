# RAG-05 — Streaming de respostas via SSE — Tasks

**Design:** `.specs/features/rag-05-streaming-sse/design.md`
**Spec:** `.specs/features/rag-05-streaming-sse/spec.md`
**Status:** Awaiting human approval

---

## Execution Plan

### Phase 1: Foundation (Parallel OK)

Config, chain de streaming, helpers SSE e client de UI são independentes.

```
T1 ──┐
T2 ──┼──→ T4
T3 ──┘
T5 ──→ T6
```

### Phase 2: Core Implementation (Parallel OK)

O endpoint backend (T4) e a renderização de UI (T6) são independentes — cada um com seus testes co-localizados.

```
T1,T2,T3 done:  T4 [P]
T5 done:        T6 [P]
```

### Phase 3: Integration (Sequential)

Aceite end-to-end depende do backend e do frontend.

```
T4, T6 done:  T7
```

---

## Task Breakdown

### T1: [Adicionar setting generation_streaming_enabled + .env.example + testes]

**What**: Adicionar `generation_streaming_enabled: bool = Field(default=False)` ao bloco Generation de `config.py`, documentar em `.env.example` e escrever testes de default/override.
**Where**: `src/medasist/config.py`, `.env.example`, `tests/config/test_config.py` (modify)
**Depends on**: None
**Reuses**: padrão `Field(default=False)` dos flags RAG-01/02/03
**Requirement**: RQ-05-06

**Tools**:
- MCP: NONE
- Skill: NONE

**Done when**:
- [ ] Default `generation_streaming_enabled=False`; override por env `GENERATION_STREAMING_ENABLED=true` funciona (RQ-05-06)
- [ ] `.env.example` documenta `GENERATION_STREAMING_ENABLED=false` (comentário "off por padrão — byte-identical")
- [ ] Gate check passa: `pytest tests/config/test_config.py -v`
- [ ] Test count: N novos testes passam (sem deleção silenciosa)

**Tests**: unit
**Gate**: quick

> Nota (test co-location): `TESTING.md` marca `config.py` como "none (indirect via fixtures)". Segue-se o precedente do RAG-01/03 que adiciona `tests/config/test_config.py` como melhoria da lacuna L1.

---

### T2: [Criar stream_answer e build_stream_chain em chain.py + testes] [P]

**What**: Adicionar em `chain.py`: `stream_answer(question, stores, profile, settings=None, doc_types=None) -> Generator[str, None, tuple[list[CitationItem], bool]]` que (a) recupera via `build_retriever(select_collections(stores, doc_types))`, (b) decide cold start **antes** de qualquer delta sem chamar o LLM, (c) gera deltas via `prompt | ChatOpenAI(perfil) | StrOutputParser().stream(...)` acumulando o texto, (d) roda `validate_citations` ao final retornando `(citations, is_cold_start)`; e `build_stream_chain(stores, profile, settings=None)` retornando closure `stream(question, doc_types=None)` (espelho de `build_chain`). Escrever `tests/generation/test_chain.py`.
**Where**: `src/medasist/generation/chain.py` (modify), `tests/generation/test_chain.py` (modify)
**Depends on**: None
**Reuses**: `run_query` (estrutura), `build_retriever`/`select_collections`, `build_citations`/`validate_citations`, `get_profile_config`, `PromptRegistry`, `ChatOpenAI`
**Requirement**: RQ-05-01, RQ-05-03, RQ-05-04, RQ-05-07, RQ-05-08, RQ-05-09

**Tools**:
- MCP: NONE
- Skill: NONE

**Done when**:
- [ ] Deltas yieldados concatenam exatamente na resposta do LLM (mock do `ChatOpenAI`/`.stream`) — RQ-05-01
- [ ] Perfil respeitado: `get_profile_config`/`PromptRegistry` usados (assert de chamada) — RQ-05-03
- [ ] Cold start (docs vazio): nenhum delta, `mock_llm_cls.assert_not_called()`, retorno `([], True)` — RQ-05-07
- [ ] Resposta sem citação válida → retorno `([], True)` — RQ-05-08
- [ ] `doc_types` limita stores via `select_collections` mockado (chamado com o filtro) — RQ-05-04
- [ ] Falha do `.stream()` propaga a exceção (não capturada aqui) — RQ-05-09
- [ ] Gate check passa: `pytest tests/generation/test_chain.py -v`
- [ ] Test count: N testes passam (existentes + novos; sem deleção silenciosa)

**Tests**: unit
**Gate**: quick

---

### T3: [Adicionar helpers de serialização SSE em schemas.py + testes] [P]

**What**: Adicionar em `api/schemas.py` helpers que montam a string `data: {json}\n\n` de cada evento SSE (`sse_token(delta)`, `sse_citations(items)`, `sse_disclaimer(text)`, `sse_cold_start(message)`, `sse_error(message)`, `sse_done()`) usando `json.dumps(..., ensure_ascii=False)` e `CitationResponse`/`CitationResponse.from_item` para o evento `citations`. Escrever `tests/api/test_sse_helpers.py`.
**Where**: `src/medasist/api/schemas.py` (modify), `tests/api/test_sse_helpers.py` (new)
**Depends on**: None
**Reuses**: `CitationResponse`, `CitationResponse.from_item`
**Requirement**: RQ-05-02

**Tools**:
- MCP: NONE
- Skill: NONE

**Done when**:
- [ ] `sse_token("a")` → `data: {"type":"token","delta":"a"}\n\n`
- [ ] `sse_citations` serializa lista de `CitationResponse` no payload `citations`
- [ ] `sse_disclaimer`/`sse_cold_start`/`sse_error`/`sse_done` produzem o JSON exato do schema
- [ ] UTF-8 preservado (`ensure_ascii=False`); cada evento termina em `\n\n`
- [ ] Gate check passa: `pytest tests/api/test_sse_helpers.py -v`
- [ ] Test count: N testes passam (sem deleção silenciosa)

**Tests**: unit
**Gate**: quick

> Nota (test co-location): `TESTING.md` marca `api/schemas.py` como "none". Os helpers SSE são lógica real de framing JSON e ganham arquivo de teste dedicado (melhoria da lacuna L1), sem depender do endpoint.

---

### T4: [Criar endpoint POST /query/stream + wiring no lifespan + testes de API] [P]

**What**: Em `api/main.py`, construir `app.state.streaming_chains = {profile: build_stream_chain(stores, profile, settings) for profile in UserProfile}` no lifespan. Em `api/routers/query.py`, criar `def query_stream(request, body)` com `@limiter.limit("10/minute")` que: guard 404 se `not settings.generation_streaming_enabled`; retorna `StreamingResponse(gen, media_type="text/event-stream")`; `gen` consome `app.state.streaming_chains[profile](question, doc_types)`, emite `token` por delta, checa `request.is_disconnected()` a cada iteração (interrompe), captura erro a meio (emite `error` terminal sem `done`), e ao final emite `citations`+`disclaimer`+`done` (ou `cold_start`+`disclaimer`+`done`). Atualizar `tests/api/conftest.py` (fixture de chain streamada + `streaming_client` com flag on), `tests/api/test_query.py` (`TestQueryStream`) e `tests/api/test_query_logging.py`.
**Where**: `src/medasist/api/main.py` (modify), `src/medasist/api/routers/query.py` (modify), `tests/api/conftest.py` (modify), `tests/api/test_query.py` (modify), `tests/api/test_query_logging.py` (modify)
**Depends on**: T1, T2, T3
**Reuses**: `limiter`, `_rate_limit_exceeded_handler`, helpers SSE (T3), `build_stream_chain` (T2), `QueryRequest`
**Requirement**: RQ-05-01, RQ-05-02, RQ-05-03, RQ-05-04, RQ-05-06, RQ-05-07, RQ-05-08, RQ-05-09, RQ-05-10, RQ-05-11, RQ-05-12

**Tools**:
- MCP: NONE
- Skill: NONE

**Done when**:
- [ ] Flag on + chain streamada mockada → resposta `text/event-stream`; deltas de `token` concatenam na resposta (RQ-05-01); terminais `citations`+`disclaimer`+`done` (RQ-05-02)
- [ ] Perfil (RQ-05-03) e `doc_types` (RQ-05-04) repassados à closure streamada
- [ ] Flag off → 404 sem iniciar gerador; `/query` byte-identical (RQ-05-06)
- [ ] Cold start pré-stream → `cold_start`+`disclaimer`+`done`, sem token (RQ-05-07)
- [ ] Resposta sem citação válida → `cold_start` terminal (RQ-05-08)
- [ ] Falha a meio → `error` terminal sem `done` (RQ-05-09)
- [ ] `request.is_disconnected()` interrompe o gerador (RQ-05-10)
- [ ] Rate limit → 429 antes de qualquer byte SSE (RQ-05-11)
- [ ] `""` → 422 igual ao `/query` (RQ-05-12)
- [ ] Logging do `/query/stream` (profile, cold_start, citations, latency) sem vazar dados
- [ ] Gate check passa: `pytest tests/api/ -v --cov=src --cov-fail-under=80`
- [ ] Test count: N testes passam (existentes + novos; sem deleção silenciosa)

**Tests**: integration
**Gate**: full

---

### T5: [Adicionar StreamEvent e query_stream em ui/client.py + testes] [P]

**What**: Em `ui/client.py`, adicionar `@dataclass(frozen=True) class StreamEvent: type; delta; citations; message` e `query_stream(question, profile, doc_types=None, base_url=None, timeout=DEFAULT_TIMEOUT) -> Generator[StreamEvent, None, None]` que usa `httpx.Client.stream("POST", "{base}/query/stream", json=payload)`, itera `response.iter_lines()`, parseia linhas `data: {json}` em `StreamEvent`, e trata 429→`RateLimitError`, 5xx→`ServerError` (reusando exceções existentes). Escrever `tests/ui/test_client.py`.
**Where**: `src/medasist/ui/client.py` (modify), `tests/ui/test_client.py` (modify)
**Depends on**: None
**Reuses**: `CitationResult`, `RateLimitError`/`ServerError`/`APIError`, `get_settings`
**Requirement**: RQ-05-05, RQ-05-06

**Tools**:
- MCP: NONE
- Skill: NONE

**Done when**:
- [ ] `query_stream` yield eventos tipados a partir de linhas `data:` (mock de `httpx.Client.stream` com `iter_lines`)
- [ ] Deltas `token` acumulam; eventos terminais (`citations`/`disclaimer`/`cold_start`/`error`/`done`) expostos com os campos corretos
- [ ] 429 → `RateLimitError`; 5xx → `ServerError` (RQ-05-06)
- [ ] Gate check passa: `pytest tests/ui/test_client.py -v`
- [ ] Test count: N testes passam (existentes + novos; sem deleção silenciosa)

**Tests**: unit
**Gate**: quick

---

### T6: [Renderizar streaming em ui/app.py via st.write_stream + testes]

**What**: Em `ui/app.py`, quando `settings.generation_streaming_enabled`, consumir `query_stream` num gerador que (a) acumula `answer` e captura terminais (`citations`/`disclaimer`/`is_cold_start`/`error`) via closure, (b) yield apenas deltas para `st.write_stream`. Após concluir: `error` → não persiste (tratamento de erro); `cold_start` → descarta o texto streamado, mostra `cold_start_message` + disclaimer; sucesso → renderiza citações + disclaimer e reconstrui `QueryResult` no histórico. Escrever `tests/ui/test_app.py`.
**Where**: `src/medasist/ui/app.py` (modify), `tests/ui/test_app.py` (modify)
**Depends on**: T5
**Reuses**: `query_stream`, `StreamEvent`, `QueryResult`/`CitationResult`, `_render_response`/`_format_citation`/`_handle_error`
**Requirement**: RQ-05-05, RQ-05-08, RQ-05-09

**Tools**:
- MCP: NONE
- Skill: NONE

**Done when**:
- [ ] `st.write_stream` chamado com gerador de deltas; texto renderizado incrementalmente (RQ-05-05)
- [ ] Sucesso: citações + disclaimer renderizados e `QueryResult` adicionado ao histórico (RQ-05-05)
- [ ] `cold_start` terminal: texto streamado descartado, `cold_start_message` exibido (RQ-05-08)
- [ ] `error` terminal: resposta parcial NÃO persistida no histórico (RQ-05-09)
- [ ] Gate check passa: `pytest tests/ui/test_app.py -v`
- [ ] Test count: N testes passam (existentes + novos; sem deleção silenciosa)

**Tests**: unit
**Gate**: quick

> Nota: `TESTING.md` marca `ui/app.py` como "none (coverage-excluded, Streamlit app)". `test_app.py` já existe e é mantido; os novos testes usam `st` mockado para validar a lógica de decisão de persistir/descartar.

---

### T7: [Criar teste de aceite test_rag05_streaming_sse.py]

**What**: Criar `tests/acceptance/test_rag05_streaming_sse.py` espelhando `test_rag03_query_rewrite.py`: com `generation_streaming_enabled=True`, chain streamada mockada (nunca rede), `TestClient` lendo `text/event-stream` linha a linha e reconstruindo a resposta — cobrindo RQ-05-01..RQ-05-12; e com flag off, `/query/stream` → 404 e `/query` byte-identical.
**Where**: `tests/acceptance/test_rag05_streaming_sse.py` (new)
**Depends on**: T4, T6
**Reuses**: fixtures de `tests/api/conftest.py`, `_FakeEmbeddings`/`_DivergentEmbeddings` e padrão de `test_rag03_query_rewrite.py`
**Requirement**: RQ-05-01, RQ-05-02, RQ-05-03, RQ-05-04, RQ-05-05, RQ-05-06, RQ-05-07, RQ-05-08, RQ-05-09, RQ-05-10, RQ-05-11, RQ-05-12

**Tools**:
- MCP: NONE
- Skill: NONE

**Done when**:
- [ ] Flag on: deltas concatenados = resposta; terminais corretos; cold start sem LLM; erro a meio → `error`; desconexão interrompe; 429 antes de bytes; `""` → 422 (RQ-05-01..RQ-05-12)
- [ ] Flag off: `/query/stream` → 404; `/query` byte-identical (RQ-05-06)
- [ ] Gate check passa: `pytest tests/acceptance/test_rag05_streaming_sse.py -v`
- [ ] Gate build passa: `black src/ tests/ scripts/ && ruff check src/ tests/ scripts/ && pytest tests/ -v --cov=src --cov-fail-under=80`
- [ ] Test count: N testes passam (sem deleção silenciosa)

**Tests**: integration
**Gate**: build

---

## Parallel Execution Map

```
Phase 1 (Parallel):
  T1 [P] ─┐
  T2 [P] ─┼──→ T4
  T3 [P] ─┘
  T5 [P] ──→ T6

Phase 2 (Parallel):
  T4 [P]  (dep T1, T2, T3)
  T6 [P]  (dep T5)

Phase 3 (Sequential):
  T7 (dep T4, T6)
```

**Parallelism constraint:** T1, T2, T3 e T5 são independentes (testes paralelo-safe conforme TESTING.md — `config.py`, `chain.py`, `schemas.py`, `ui/client.py`). T4 (endpoint + wiring) depende de T1+T2+T3; T6 (renderização) depende de T5; T4 e T6 são independentes entre si e podem rodar em paralelo. T7 (aceite) depende de T4 e T6.

---

## Pre-Approval Validation

### Check 1: Task Granularity

| Task | Scope | Status |
|------|-------|--------|
| T1 | 1 setting + env + teste | ✅ Granular |
| T2 | 1 módulo (chain) + testes | ✅ Granular |
| T3 | 1 bloco de helpers SSE + teste dedicado | ✅ Granular |
| T4 | 1 endpoint + wiring + testes de API co-localizados | ✅ Granular (um entregável: endpoint) |
| T5 | 1 client (client.py) + testes | ✅ Granular |
| T6 | 1 renderização (app.py) + testes | ✅ Granular |
| T7 | 1 arquivo de teste de aceite | ✅ Granular |

### Check 2: Diagram-Definition Cross-Check

| Task | Depends On (body) | Diagram Shows | Status |
|------|-------------------|---------------|--------|
| T1 | None | — | ✅ Match |
| T2 | None | — | ✅ Match |
| T3 | None | — | ✅ Match |
| T4 | T1, T2, T3 | T1/T2/T3 → T4 | ✅ Match |
| T5 | None | — | ✅ Match |
| T6 | T5 | T5 → T6 | ✅ Match |
| T7 | T4, T6 | T4, T6 → T7 | ✅ Match |

### Check 3: Test Co-location Validation

| Task | Code Layer Created/Modified | Matrix Requires | Task Says | Status |
|------|----------------------------|-----------------|-----------|--------|
| T1 | `config.py` (setting) | none (indirect) | unit (novo test) | ✅ OK (melhoria L1) |
| T2 | `generation/chain.py` | unit | unit | ✅ OK |
| T3 | `api/schemas.py` (helpers) | none | unit (novo test dedicado) | ✅ OK (melhoria L1) |
| T4 | `api/routers/query.py` + `api/main.py` | integration | integration | ✅ OK |
| T5 | `ui/client.py` | unit | unit | ✅ OK |
| T6 | `ui/app.py` | none (coverage-excluded) | unit (test_app existente) | ✅ OK |
| T7 | `tests/acceptance/` (test-only) | n/a | integration | ✅ OK |

---

## Task Verification Standards (gate commands)

| Gate | Command |
|------|---------|
| Quick | `pytest tests/<module> -v` |
| Full | `pytest tests/ -v --cov=src --cov-fail-under=80` |
| Build (fim do feature) | `black src/ tests/ scripts/ && ruff check src/ tests/ scripts/ && pytest tests/ -v --cov=src --cov-fail-under=80` |

Commit format (português, imperativo, conforme AGENTS.md): `feat(api): adiciona endpoint POST /query/stream (SSE)`, `feat(generation): adiciona stream_answer com validação de citações`, `feat(ui): renderiza respostas streaming via st.write_stream`, etc. Antes de abrir PR: rodar skill `code-reviewer` (AGENTS.md).
