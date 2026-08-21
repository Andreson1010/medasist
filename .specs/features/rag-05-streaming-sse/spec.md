# RAG-05 — Streaming de respostas via SSE — Technical Spec

**Path:** `.specs/features/rag-05-streaming-sse/spec.md`
**TLC scope:** large
**Based on story:** "stream LLM answer progressively via Server-Sent Events from a dedicated endpoint, render incrementally in Streamlit via st.write_stream, preserving cold start / citation validation / disclaimer / profiles / doc_types / rate limiting; off-by-default byte-identical."
**Status:** Awaiting human approval

---

## Problem Statement

Hoje a UI espera a resposta completa do `POST /query` (bloqueio de 10–60s+ até o LLM local terminar) e só então renderiza tudo de uma vez. RAG-05 introduz um endpoint dedicado **`POST /query/stream`** que entrega a resposta do LLM progressivamente via **Server-Sent Events (SSE)**, e a UI renderiza incrementalmente com `st.write_stream`. A feature é **desabilitada por padrão** (`generation_streaming_enabled=False`): quando off, `POST /query` permanece **byte-identical** (nenhuma alteração de comportamento ou contrato) e `/query/stream` responde 404. Quando on, preserva integralmente as invariantes de segurança do MedAssist: **cold start** (retrieval vazio → sem token, sem chamada ao LLM), **validação de citações** (resposta sem citação válida → cold start terminal), **disclaimer**, **perfil** (temperatura/max_tokens/prompt), **filtro doc_types** e **rate limiting**.

## Goals

- [ ] Entregar a resposta do LLM em eventos SSE tipados (`token`/`citations`/`disclaimer`/`cold_start`/`error`/`done`) via `POST /query/stream`, cujos deltas concatenados são idênticos à resposta do LLM (RQ-05-01/02).
- [ ] Renderizar a resposta incrementalmente na UI via `st.write_stream`, reconstruindo `QueryResult` no histórico ao final (RQ-05-05).
- [ ] Preservar todas as invariantes de segurança (cold start, citações, disclaimer, perfil, doc_types, rate limit) no caminho de streaming (RQ-05-03/04/07/08/09/11).
- [ ] Manter identidade total quando `generation_streaming_enabled=False` (default): `POST /query` byte-identical, `/query/stream` → 404, UI usa `/query` (RQ-05-06).
- [ ] Tratar desconexão do cliente (RQ-05-10) e rejeitar pergunta vazia com a mesma validação do `/query` (RQ-05-12).

## Out of Scope

| Feature | Reason |
|---------|--------|
| Alterar o contrato/behaviour do `POST /query` | Byte-identical obrigatório quando off (RQ-05-06) |
| Streaming via WebSocket / chunked HTTP arbitrário | Escopo definido: SSE (`text/event-stream`) |
| Heartbeat/keep-alive periódico do SSE | Não necessário no MVP: deltas do LLM local fluem dentro do `llm_request_timeout` (decisão Q6) |
| Multiturno / histórico no backend | Streaming é só transporte; o estado continua na UI |
| Cancelamento/controle de backpressure avançado | Desconexão simples via `request.is_disconnected()` (Q4) |
| Mudar a validação de `QueryRequest` (ex: rejeitar whitespace-only) | Alterar `QueryRequest` mudaria o `/query` (violaria byte-identical); fora do escopo (Q5) |
| Avaliação RAGAS via streaming | A avaliação continua usando `run_query`/`retrieve` (não-SSE) — invariante AD-011 |

---

## User Stories

### P1: Streaming da resposta via SSE ⭐ MVP

**User Story**: Como profissional de saúde usando a UI, quero ver a resposta do RAG aparecendo progressivamente enquanto o LLM gera (em vez de esperar tudo de uma vez), com as mesmas garantias de segurança, para reduzir a latência percebida sem perder citações, disclaimer ou cold start.

**Why P1**: É o caso de uso central da story aprovada (melhora a percepção de latência do LLM local).

**Acceptance Criteria**:

1. WHEN `generation_streaming_enabled=True` E uma consulta normal é enviada a `POST /query/stream` THEN system SHALL responder com `Content-Type: text/event-stream` e emitir um ou mais eventos `{"type":"token","delta":...}` cuja concatenação dos `delta` é exatamente a resposta do LLM. *(RQ-05-01)*
2. WHEN o streaming completa com citações válidas THEN system SHALL emitir, após os tokens, os eventos terminais `{"type":"citations","citations":[...]}` + `{"type":"disclaimer","text":...}` + `{"type":"done"}`. *(RQ-05-02)*
3. WHEN o streaming roda para um `profile` THEN system SHALL respeitar `temperature`, `max_tokens` e `prompt_template` desse perfil (mesmo `get_profile_config` e `PromptRegistry` do `run_query`). *(RQ-05-03)*
4. WHEN `doc_types` é informado THEN system SHALL limitar a recuperação às coleções selecionadas (via `select_collections`) **antes** de emitir qualquer token. *(RQ-05-04)*
5. WHEN a UI consome o streaming com `generation_streaming_enabled=True` THEN system SHALL renderizar os deltas incrementalmente via `st.write_stream`, e, ao concluir, renderizar citações + disclaimer e reconstruir `QueryResult` no histórico da sessão. *(RQ-05-05)*
6. WHEN `generation_streaming_enabled=False` (default) THEN system SHALL manter `POST /query` byte-identical, responder 404 (ou 4xx) em `POST /query/stream` e fazer a UI usar `/query`. *(RQ-05-06)*
7. WHEN o retrieval retorna vazio (cold start) THEN system SHALL NÃO emitir nenhum token nem chamar o LLM, e emitir `{"type":"cold_start","message":...}` + `{"type":"disclaimer","text":...}` + `{"type":"done"}`. *(RQ-05-07)*
8. WHEN a resposta acumulada não tem citações válidas THEN system SHALL emitir o evento terminal `cold_start` e a UI SHALL descartar o texto já streamado e mostrar `cold_start_message`. *(RQ-05-08)*
9. WHEN o LM Studio falha no meio do streaming THEN system SHALL emitir o evento terminal `{"type":"error","message":...}` e a UI SHALL NÃO persistir a resposta parcial. *(RQ-05-09)*
10. WHEN o cliente desconecta no meio do streaming THEN system SHALL interromper o gerador (não continuar gerando). *(RQ-05-10)*
11. WHEN o rate limit é excedido THEN system SHALL responder 429 **antes** de qualquer byte SSE. *(RQ-05-11)*
12. WHEN a pergunta é vazia/em branco THEN system SHALL rejeitá-la com a **mesma validação** do `POST /query` (reuso de `QueryRequest` → 422). *(RQ-05-12)*

**Independent Test**: `pytest tests/acceptance/test_rag05_streaming_sse.py` — com `generation_streaming_enabled=True`, chain streamada mockada, `TestClient` lendo `text/event-stream` linha a linha e reconstruindo a resposta; com flag off, `tests/api/test_query.py` passa integralmente (byte-identical) e `POST /query/stream` retorna 404.

---

### P1: Segurança e robustez do streaming

**User Story**: Como mantenedor, quero que o streaming reutilize o máximo do pipeline existente (retrieval, citações, perfis, disclaimer, rate limit) e degrade graciosamente (erro/cold start) sem nunca violar as regras de segurança médica, para ativar a feature com segurança.

**Why P1**: O valor da feature depende de não quebrar cold start, citações ou disclaimer — as invariantes inegociáveis (AGENTS.md).

**Acceptance Criteria**:

1. WHEN o streaming decide cold start por retrieval vazio THEN system SHALL decidir **antes** de emitir qualquer token, sem chamar o LLM. *(RQ-05-07)*
2. WHEN o streaming acumula a resposta completa THEN system SHALL rodar `validate_citations` sobre o texto completo; sem citações válidas → cold start terminal. *(RQ-05-02, RQ-05-08)*
3. WHEN o endpoint de streaming é chamado desabilitado THEN system SHALL responder 404 (via guard no handler), sem iniciar nenhum gerador. *(RQ-05-06)*
4. WHEN há erro do LLM a meio THEN system SHALL emitir `error` terminal (em vez de `done`) e registrar via `logger.exception`. *(RQ-05-09)*

**Independent Test**: `pytest tests/api/test_query.py::TestQueryStream` e `tests/generation/test_chain.py` — cold start pré-stream (nenhum token, `mock_llm_cls.assert_not_called()`), resposta sem citação → cold start terminal, falha a meio → `error`, flag off → 404.

---

## Edge Cases

- WHEN `generation_streaming_enabled=False` THEN `POST /query/stream` SHALL responder 404 **antes** de qualquer leitura do body/gerador.
- WHEN a resposta acumulada contém marcador `[N]` sem `CitationItem` correspondente (alucinação) THEN system SHALL remover o marcador via `validate_citations`; se restar zero citações válidas → cold start terminal (RQ-05-08).
- WHEN o LM Studio lança exceção a meio THEN system SHALL emitir `{"type":"error"}` terminal (sem `done`) e a UI descarta o parcial (RQ-05-09).
- WHEN `request.is_disconnected()` é `True` durante o loop THEN system SHALL interromper o gerador (return) sem emitir terminais (RQ-05-10).
- WHEN a pergunta é `""` THEN `QueryRequest.min_length=1` rejeita com 422, igual ao `/query` (RQ-05-12). **Whitespace-only** (`"  "`) segue o comportamento atual do `/query` (passa `min_length`); NÃO é alterado aqui para preservar byte-identical (Q5).
- WHEN o client envia `doc_types` vazio/None THEN consulta todas as coleções, igual ao `/query`.
- WHEN cold start por retrieval vazio THEN nenhum token; `cold_start` + `disclaimer` + `done` (RQ-05-07).
- WHEN cold start por citação inválida (tokens já streamados) THEN evento `cold_start` terminal; UI descarta o texto (RQ-05-08).

---

## Requirement Traceability

| Requirement ID | Story | Phase | Status |
|----------------|-------|-------|--------|
| RQ-05-01 | P1: Streaming | Design | Pending |
| RQ-05-02 | P1: Streaming + P1: Segurança | Design | Pending |
| RQ-05-03 | P1: Streaming | Design | Pending |
| RQ-05-04 | P1: Streaming | Design | Pending |
| RQ-05-05 | P1: Streaming | Design | Pending |
| RQ-05-06 | P1: Streaming + P1: Segurança | Design | Pending |
| RQ-05-07 | P1: Streaming + P1: Segurança | Design | Pending |
| RQ-05-08 | P1: Streaming + P1: Segurança | Design | Pending |
| RQ-05-09 | P1: Streaming + P1: Segurança | Design | Pending |
| RQ-05-10 | P1: Streaming | Design | Pending |
| RQ-05-11 | P1: Streaming | Design | Pending |
| RQ-05-12 | P1: Streaming | Design | Pending |

**ID format:** `RQ-05-[NUMBER]` — prefixo da sub-feature RAG-05.
**Coverage:** 12 total, mapeados 1:1 aos ACs aprovados (AC1–AC12). ✅

---

## Data Model / Settings Changes

Sem modelos persistidos novos (nenhuma migração; contrato `QueryRequest`/`QueryResponse` inalterado). Novo campo de configuração no bloco **Generation** de `src/medasist/config.py` (ao lado de `disclaimer`/`cold_start_message` ou novo bloco `# Generation`):

| Field | Type | Default | Env var | Constraint | Rationale |
|-------|------|---------|---------|------------|-----------|
| `generation_streaming_enabled` | `bool` | `False` | `GENERATION_STREAMING_ENABLED` | — | Flag off por padrão (identidade/byte-identical; precedente RAG-01/02/03) |

- **Não** adicionar `streaming_chunk_size`/heartbeat neste MVP (Q6) — os deltas fluem dentro de `llm_request_timeout`.
- Nenhum dado de paciente; nenhuma coluna/schema novo em ChromaDB.

## Process / Background Flow

**Happy path (streaming habilitado + consulta normal):**
1. `POST /query/stream` recebe `QueryRequest` (mesma validação → 422 se inválido; RQ-05-12).
2. Guard desabilitado: se `not settings.generation_streaming_enabled` → `HTTPException(404)` (RQ-05-06).
3. Handler `def query_stream(...)` retorna `StreamingResponse(gen, media_type="text/event-stream")`; FastAPI roda o `def` no threadpool (precedente L-004).
4. `gen` consome `stream_answer(...)` de `chain.py` (protocolo-agnóstico): recupera docs com `build_retriever(select_collections(stores, doc_types))`.
5. **Cold start pré-stream** (docs vazio): nenhum delta; retorna `([], is_cold_start=True)`.
6. **Normal**: constrói citações, monta `prompt | ChatOpenAI(perfil) | StrOutputParser`, faz `.stream({context, question})` — **acumula** o texto e **yield cada delta**.
7. Ao final, `validate_citations(resposta_completa, citações)`: válidas → `(valid_citations, False)`; inválidas → `([], True)` (RQ-05-02/08).
8. O wrapper SSE de `query.py`: para cada delta → `{"type":"token","delta":...}`; lê o retorno do gerador: se cold start → `cold_start` + `disclaimer` + `done`; senão → `citations` + `disclaimer` + `done` (RQ-05-02/07/08).

**Failure path — LLM falha a meio:** exceção do `.stream()` propaga do `stream_answer`; o wrapper SSE captura, loga `logger.exception` e emite `{"type":"error","message":...}` (terminal; **sem** `done`). Nenhum byte é emitido após `error` (RQ-05-09).

**Failure path — cliente desconecta:** a cada iteração o wrapper verifica `request.is_disconnected()`; se `True`, interrompe (return) sem emitir terminais (RQ-05-10).

**Failure path — rate limit:** `@limiter.limit("10/minute")` no `def query_stream` → `RateLimitExceeded` tratado pelo handler global → **429 JSON antes de qualquer byte SSE** (RQ-05-11).

## API Changes

Novo endpoint (mesma família do `/query`), **sem alterar** `/query`, `/ingest` ou `/health`:

| Method | Path | Body | Response | Rate limit |
|--------|------|------|----------|------------|
| POST | `/query/stream` | `QueryRequest` (reusado) | `text/event-stream` (SSE) | `10/minute` (counter separado — Q3) |

**SSE event schema** (eventos tipados em JSON; **um** `data:` por evento, separados por linha em branco `\n\n`; media type `text/event-stream`):

| Evento | Payload (`data:` line) | Terminal? |
|--------|------------------------|-----------|
| `token` | `{"type":"token","delta":"<texto parcial>"}` | não |
| `citations` | `{"type":"citations","citations":[<CitationResponse JSON>]}` | não |
| `disclaimer` | `{"type":"disclaimer","text":"<disclaimer>"}` | não |
| `cold_start` | `{"type":"cold_start","message":"<cold_start_message>"}` | não* |
| `error` | `{"type":"error","message":"<msg>"}` | **sim** (substitui `done`) |
| `done` | `{"type":"done"}` | **sim** |

\* `cold_start` não é "terminal" isoladamente: sempre seguido de `disclaimer` + `done` (RQ-05-07). `citations`/`disclaimer` também não são terminais sozinhos; a sequência terminal de sucesso é `citations` + `disclaimer` + `done` (RQ-05-02). Em erro, apenas `error` (RQ-05-09).

- `CitationResponse` (schemas.py) é reutilizado para serializar cada citação no evento `citations`.

## Frontend Changes

- `src/medasist/ui/client.py`: novo `StreamEvent` (dataclass) e `query_stream(...)` — gerador que faz `httpx.Client.stream("POST", "{base}/query/stream", json=payload)` e parseia linhas `data: {...}` em `StreamEvent`, tratando 429/5xx como hoje.
- `src/medasist/ui/app.py`: quando `settings.generation_streaming_enabled`, usar o caminho de streaming: um gerador que filtra deltas para `st.write_stream` (acumulando a resposta e capturando os terminais por closure); ao concluir, decide por estado terminal — `cold_start` → descarta o texto e mostra `cold_start_message`; `error` → não persiste; sucesso → renderiza citações + disclaimer e reconstrui `QueryResult` no histórico (RQ-05-05/08/09).

## Tests Required

**Unit:**
- `tests/generation/test_chain.py` (modify): `stream_answer` — deltas concatenados = resposta (RQ-05-01), perfil respeitado via `get_profile_config`/`PromptRegistry` (RQ-05-03), cold start pré-stream sem chamar o LLM (`mock_llm_cls.assert_not_called()`) (RQ-05-07), resposta sem citação válida → `([], True)` (RQ-05-08), doc_types limita stores via `select_collections` mockado (RQ-05-04), falha do `.stream()` propaga (RQ-05-09).
- `tests/config/test_config.py` (modify): `TestSettingsGenerationStreaming` — default `generation_streaming_enabled=False`, override por env `GENERATION_STREAMING_ENABLED=true` (RQ-05-06).

**Integration (API):**
- `tests/api/conftest.py` (modify): fixture de chain streamada (retorna deltas via `MagicMock` + side_effect de gerador) e `streaming_client` (flag on).
- `tests/api/test_query.py` (modify): `TestQueryStream` — happy path SSE (parse de `data:` linhas; deltas concatenados) (RQ-05-01), terminais `citations`+`disclaimer`+`done` (RQ-05-02), perfil (RQ-05-03), doc_types (RQ-05-04), cold start pré-stream sem LLM (RQ-05-07), resposta sem citação → `cold_start` terminal (RQ-05-08), erro a meio → `error` terminal sem `done` (RQ-05-09), desconexão interrompe gerador (RQ-05-10), 429 antes de bytes SSE (RQ-05-11), `""` → 422 (RQ-05-12), flag off → 404 (RQ-05-06).
- `tests/api/test_query_logging.py` (modify): log do `/query/stream` (profile, cold_start, citations, latency) sem vazar dados.

**Frontend:**
- `tests/ui/test_client.py` (modify): `query_stream` — parse de `data:` em `StreamEvent`, 429 → `RateLimitError`, 5xx → `ServerError`, tokens/delta acumulam.
- `tests/ui/test_app.py` (modify): caminho de streaming com `st.write_stream` — sucesso persiste `QueryResult` no histórico; `cold_start` descarta texto e mostra `cold_start_message`; `error` não persiste parcial.

**Aceite:**
- `tests/acceptance/test_rag05_streaming_sse.py` (new, espelhando `test_rag03_query_rewrite.py`): fluxo completo com flag on/off, chain streamada mockada (nunca rede), `TestClient` lendo SSE — cobrindo RQ-05-01..RQ-05-12.

**Testes existentes que devem continuar verdes:** `tests/api/test_query.py`, `tests/api/test_query_logging.py`, `tests/generation/test_chain.py`, `tests/ui/test_client.py`, `tests/ui/test_app.py`, `tests/config/test_config.py` — flag off por padrão garante byte-identical (RQ-05-06).

## Files That Will Change

| File | Change type | Why |
|------|-------------|-----|
| `src/medasist/generation/chain.py` | Modify | Adicionar `stream_answer(...)` (gerador protocolo-agnóstico que yield deltas e retorna `(citations, is_cold_start)`) e `build_stream_chain(...)` (closure streamada para o lifespan) |
| `src/medasist/api/schemas.py` | Modify | Helpers de serialização SSE reusando `CitationResponse.from_item` (`token/citations/disclaimer/cold_start/error/done`) |
| `src/medasist/api/routers/query.py` | Modify | Novo `POST /query/stream` (`def` + `StreamingResponse` + wrapper SSE + guard 404 + desconexão + `@limiter.limit`) |
| `src/medasist/api/main.py` | Modify | Lifespan: construir `app.state.streaming_chains` (uma por `UserProfile`) |
| `src/medasist/config.py` | Modify | Campo `generation_streaming_enabled: bool = Field(default=False)` |
| `.env.example` | Modify | Documentar `GENERATION_STREAMING_ENABLED` (RQ-05-06) |
| `src/medasist/ui/client.py` | Modify | `StreamEvent` + `query_stream()` (httpx stream + parse SSE) |
| `src/medasist/ui/app.py` | Modify | Caminho de streaming com `st.write_stream`, terminais e reconstrução de `QueryResult` |
| `tests/api/conftest.py` | Modify | Fixture de chain streamada + `streaming_client` |
| `tests/api/test_query.py` | Modify | `TestQueryStream` (todos os ACs do endpoint) |
| `tests/api/test_query_logging.py` | Modify | Logging do `/query/stream` |
| `tests/generation/test_chain.py` | Modify | Unit de `stream_answer`/`build_stream_chain` |
| `tests/config/test_config.py` | Modify | `TestSettingsGenerationStreaming` |
| `tests/ui/test_client.py` | Modify | Unit de `query_stream` |
| `tests/ui/test_app.py` | Modify | Renderização streaming |
| `tests/acceptance/test_rag05_streaming_sse.py` | New | Aceite end-to-end dos ACs numerados |

## Risks

- **Byte-identical do `/query`:** qualquer mudança em `QueryRequest` ou em `run_query` quebra a identidade. **Mitigação:** `QueryRequest` reutilizado sem alteração; streaming em módulo/rota novos; flag off default (RQ-05-06).
- **Cold start contornado pelo streaming:** risco de o streaming emitir token mesmo sem contexto ou chamar o LLM em cold start. **Mitigação:** decisão de cold start **antes** de qualquer delta e sem LLM (RQ-05-07); validação de citações pós-stream força cold start terminal se inválido (RQ-05-08) — mesmo padrão do `run_query`.
- **Resposta parcial persistida pela UI em erro:** risco de o usuário/UI guardar texto incompleto. **Mitigação:** UI só persiste no histórico após estado terminal de sucesso; `error` → descarta (RQ-05-09).
- **Bloqueio do event loop (H4/CONCERNS + L-004):** chamada síncrona ao LLM não pode rodar em `async def`. **Mitigação:** `def query_stream` + `StreamingResponse` com gerador síncrono — Starlette itera o gerador no threadpool (precedente FIX-02/L-004, decisão Q2).
- **Rate limit durante streaming:** longas gerações podem colidir com o limite do `/query`. **Mitigação:** counter separado (Q3) com mesmo limite `10/minute`; 429 dispara antes de qualquer byte SSE (RQ-05-11).
- **Desconexão gera trabalho fantasma:** cliente fecha, servidor continua gerando. **Mitigação:** checagem `request.is_disconnected()` no loop; interrompe gerador (RQ-05-10).
- **Consistência flag entre API e UI:** UI precisa saber se o backend está com streaming on. **Mitigação:** UI lê `settings.generation_streaming_enabled` (mesma fonte `get_settings()`); se divergir (UI on, API off), o client de streaming recebe 404 e trata como falha/fallback (edge case documentado no design).
- **Import circular retriever↔chain:** `chain.py` já importa de `retrieval.retriever` (`build_retriever`, `select_collections`) — `stream_answer` reusa os mesmos imports, sem novo ciclo.
- **L4 (settings singleton nunca resetado):** testes do novo setting usam `Settings` instanciada com env (fixture), não `get_settings()` mutado — sem nova mutação de singleton.
- **L1 (coverage gap — `api/schemas.py`/`api/deps.py`):** o novo endpoint exercita 422, 429 e SSE via `TestClient`, reduzindo as lacunas conhecidas (429 testado aqui, RQ-05-11).

## Decisões Resolvidas (Q1–Q6 — vinculantes)

| # | Pergunta | Decisão (vinculante) |
|---|----------|----------------------|
| Q1 | Formato/estrutura do streaming | **SSE tipado**, endpoint dedicado `POST /query/stream` (mesmo `QueryRequest`). Rota `def` retornando `StreamingResponse(gen, media_type="text/event-stream")`; gerador **síncrono** iterado no threadpool do Starlette. Eventos: `token`, `citations`, `disclaimer`, `cold_start`, `error`, `done` — um `data:` por evento, `\n\n` separados. |
| Q2 | Chamada síncrona ao LLM | **Síncrona via LCEL `.stream()`** em rota `def` (threadpool) — NÃO `ainvoke`/async, pois retriever/ChromaDB são síncronos (precedente L-004/FIX-02). |
| Q3 | Rate limit | **Counter separado, mesmo limite** (`10/minute`) — um streaming longo não consome o orçamento do `/query`; 429 antes de qualquer byte SSE (RQ-05-11). |
| Q4 | Desconexão | **`request.is_disconnected()`** checado a cada iteração do loop; `True` → interrompe o gerador sem emitir terminais (RQ-05-10). |
| Q5 | Pergunta em branco | **Reusar `QueryRequest`** sem alteração → validação idêntica ao `/query` (`""` → 422; RQ-05-12). Whitespace-only segue o comportamento atual do `/query` (não alterado para preservar byte-identical). |
| Q6 | Heartbeat/chunk_size | **Não incluir neste MVP** — deltas do LLM local fluem dentro de `llm_request_timeout`; sem settings extras além de `generation_streaming_enabled`. |

---

## Open Questions

None. (Q1–Q6 resolvidas e vinculantes; registradas na tabela acima.)

---

## Success Criteria

- [ ] Com `generation_streaming_enabled=False` (default), `POST /query` é byte-identical (suíte existente verde) e `POST /query/stream` retorna 404 (RQ-05-06).
- [ ] Com flag ativa, `POST /query/stream` emite tokens cuja concatenação é a resposta do LLM e termina com `citations` + `disclaimer` + `done` (RQ-05-01/02).
- [ ] Cold start por retrieval vazio emite `cold_start` + `disclaimer` + `done`, sem token e sem LLM (RQ-05-07); resposta sem citação válida → `cold_start` terminal (RQ-05-08).
- [ ] Falha do LM Studio a meio emite `error` terminal; UI não persiste parcial (RQ-05-09).
- [ ] Desconexão interrompe o gerador (RQ-05-10); 429 antes de bytes SSE (RQ-05-11); pergunta `""` → 422 (RQ-05-12).
- [ ] UI renderiza incrementalmente via `st.write_stream` e reconstrui `QueryResult` no histórico ao concluir (RQ-05-05).
- [ ] `tests/acceptance/test_rag05_streaming_sse.py`, `tests/api/test_query.py::TestQueryStream`, `tests/ui/test_client.py`, `tests/ui/test_app.py`, `tests/generation/test_chain.py` e demais novos passam; cobertura ≥ 80% no gate full.
