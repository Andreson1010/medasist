# RAG-03 — Query Rewrite (expansão de consultas curtas) — Technical Spec

**Path:** `.specs/features/rag-03-query-rewrite/spec.md`
**TLC scope:** medium
**Based on story:** "rewrite/expand short/ambiguous queries before retrieval via the local LLM (LM Studio)."
**Status:** Awaiting human approval

---

## Problem Statement

Consultas curtas ou ambíguas ("dipirona", "febre", "dose") têm contexto semântico insuficiente para o retrieval denso por embeddings recuperar o chunk certo, gerando cold start ou contexto irrelevante. RAG-03 adiciona um passo opcional de **expansão/reescrita de consulta** via o LLM local (LM Studio) antes da busca por similaridade, dentro do funil único `retrieve()`. A reescrita é feita apenas sobre o texto usado na busca; a pergunta **original** permanece para o prompt de geração e para as citações. A feature é **desabilitada por padrão** e degrada graciosamente (identidade) em qualquer falha, sem nunca violar a invariante de cold start.

## Goals

- [ ] Permitir que consultas curtas/ambíguas, quando `retrieval_query_rewrite_enabled=True`, sejam expandidas via LLM local e usem a consulta expandida na busca por similaridade.
- [ ] Garantir que o prompt de geração e as citações sempre referenciem a pergunta **original** (AC2) e que o cold start nunca seja contornado pela reescrita (AC7/AC8).
- [ ] Preservar identidade total do comportamento atual quando `retrieval_query_rewrite_enabled=False` (default): sem chamada ao LLM, sem mudança de resultado — suíte existente verde.
- [ ] Degradar graciosamente (retornar a consulta original, erro logado) em qualquer falha ou saída inválida do LLM, sem propagar exceção (AC5/AC6).
- [ ] Manter o caminho de avaliação RAG idêntico (invariante AD-011): reescrita apenas dentro de `retrieve()`, exercida por `retrieve()`/`run_query()`/`_collect_rows()` (AC10).

## Out of Scope

| Feature | Reason |
|---------|--------|
| Expandir consultas não-curtas (sempre verbatim) | Escopo limitado a consultas curtas/ambíguas (AC1/AC4) |
| Feedback da reescrita no prompt de geração ou nas citações | Prompt/citações usam a pergunta original (AC2) |
| Multiturno / histórico de conversa | Fora do escopo desta sub-feature |
| Cache de reescritas (por query ou por fingerprint) | Sem evidência de necessidade; cold start já evita custo |
| Alterar o contrato `POST /query` (QueryRequest/QueryResponse) | Contrato da API é estável |
| Alterar o caminho denso existente (L2, threshold, dedup, guarda lexical) | Preservar comportamento quando flag off |
| Alterar/duplicar o rerank RAG-01 ou o híbrido RAG-02 | A reescrita se posiciona antes de ambos; eles consomem a consulta efetiva |
| Novas dependências de runtime | Usa `ChatOpenAI` já presente (langchain-openai) |

---

## User Stories

### P1: Expansão de consultas curtas via LLM local ⭐ MVP

**User Story**: Como profissional de saúde que digita consultas curtas ("dipirona", "febre", "dose"), quero que, quando a reescrita estiver habilitada, o sistema expanda minha pergunta curta via o LLM local e use essa expansão na busca, mantendo minha pergunta original para a geração e citações, para que o chunk certo seja recuperado sem mudar o que é apresentado a mim.

**Why P1**: É o caso de uso central da story aprovada e o motivo da sub-feature.

**Acceptance Criteria**:

1. WHEN `retrieval_query_rewrite_enabled=True` E a consulta é curta (ver heurística de comprimento — decisão Q1) THEN system SHALL enviar a consulta ao LLM local para expansão e usar a consulta expandida na busca por similaridade. *(RQ-03-01)*
2. WHEN uma consulta curta foi expandida THEN system SHALL usar a consulta expandida para o retrieval (denso e, se ativo, esparso), mas o prompt de geração e as citações SHALL referenciar a pergunta **original**. *(RQ-03-02)*
3. WHEN `retrieval_query_rewrite_enabled=False` (default) THEN system SHALL usar a consulta inalterada e NÃO realizar nenhuma chamada ao LLM (identidade). *(RQ-03-03)*
4. WHEN `retrieval_query_rewrite_enabled=True` E a consulta está em/ acima do comprimento mínimo THEN system SHALL usar a consulta verbatim, sem chamada extra ao LLM. *(RQ-03-04)*
5. WHEN `retrieval_query_rewrite_enabled=True` E a consulta é curta E a chamada ao LLM falha (erro/timeout/indisponível) THEN system SHALL usar a consulta original, logar a falha (logger.exception) e NUNCA propagar o erro. *(RQ-03-05)*
6. WHEN o LLM retorna saída vazia/whitespace/inválida THEN system SHALL usar a consulta original. *(RQ-03-06)*
7. WHEN `stores` está vazio THEN system SHALL retornar `[]` (cold start) e o LLM de reescrita NUNCA ser chamado. *(RQ-03-07)*
8. WHEN a consulta expandida recupera nada acima do threshold THEN system SHALL retornar `[]` (cold start) — a reescrita NUNCA contorna a invariante de cold start. *(RQ-03-08)*
9. WHEN a consulta reescrita é saída não-confiável do LLM THEN system SHALL ter comprimento limitado por `retrieval_query_rewrite_max_output` e o prompt de expansão SHALL proibir preâmbulo/cochicho ("Reescreva...", "Claro!..."). *(RQ-03-09)*
10. WHEN o script de avaliação roda uma consulta curta via `retrieve()`/`run_query()` THEN system SHALL exercer o mesmo caminho de reescrita (invariante AD-011) — nenhuma lógica de reescrita fora de `retrieve()`. *(RQ-03-10)*
11. WHEN novas settings de reescrita são adicionadas THEN system SHALL atualizar `.env.example` com todas as settings, defaults e constraints. *(RQ-03-11)*

**Independent Test**: `pytest tests/acceptance/test_rag03_query_rewrite.py` — com `retrieval_query_rewrite_enabled=True`, query curta "dipirona" sobre corpus de bula com embeddings divergentes (denso vazio) retorna o chunk da bula quando o LLM mockado expande para "Qual a dose de dipirona?"; com flag off, `tests/retrieval/test_retriever.py` passa integralmente e `tests/retrieval/test_query_rewrite.py` cobre heurística, degradação e prompt.

---

### P1: Heurística de comprimento e reescrita segura

**User Story**: Como mantenedor, quero uma heurística determinística para decidir se uma consulta é "curta" (elegível para expansão) e uma implementação à prova de falhas do LLM não-confiável, para que a feature seja ativável com segurança e calibrada por configuração.

**Why P1**: A heurística define o limite AC1/AC4 e a robustez (AC5/AC6/AC9) é o que torna o LLM local seguro de usar no pipeline.

**Acceptance Criteria**:

1. WHEN a heurística decide se uma consulta é curta THEN system SHALL tokenizar com expressão regular de palavras (mesmo padrão `_TOKEN_RE` do retriever, local ao módulo para evitar import circular), remover `retrieval_stopwords` e considerar curta SE o número de tokens de conteúdo for ESTRITAMENTE menor que `retrieval_query_rewrite_min_length` (default 3). *(RQ-03-01)*
2. WHEN a consulta reescrita excede `retrieval_query_rewrite_max_output` caracteres THEN system SHALL truncá-la (após strip) ao limite. *(RQ-03-09)*
3. WHEN o LLM de reescrita é instanciado THEN system SHALL construir `ChatOpenAI` como em `chain.py` (base_url, api_key do secret, modelo resolvido, `retrieval_query_rewrite_temperature`, `retrieval_query_rewrite_max_tokens`, `llm_max_retries`, `llm_request_timeout`) com temperatura baixa para determinismo (decisão Q3). *(RQ-03-01)*

**Independent Test**: `pytest tests/retrieval/test_query_rewrite.py` — heurística (curta vs não-curta no limite exato de `min_length`), truncamento por `max_output`, identidade flag off, degradação em falha do `ChatOpenAI` mockado no local real do módulo.

---

## Edge Cases

- WHEN consulta curta e LLM retorna texto com preâmbulo ("Claro! Aqui está a pergunta: ...") THEN system SHALL aplicar o prompt que proíbe preâmbulo/cochicho; se ainda assim a saída for inválida/irrelevante, fallback à consulta original (segurança AC6/AC9).
- WHEN `stores` vazio THEN system SHALL retornar `[]` antes de qualquer reescrita — LLM de reescrita nunca chamado (AC7).
- WHEN a consulta expandida recupera nada acima do threshold THEN system SHALL cold start `[]`; `run_query` devolve `cold_start_message` sem chamar o LLM de geração (AC8).
- WHEN consulta curta mas flag off THEN system SHALL identidade sem nenhuma chamada ao LLM.
- WHEN consulta com exatamente `retrieval_query_rewrite_min_length` tokens de conteúdo THEN system SHALL NÃO reescrever (limite estrito `<`; AC4 verbatim).
- WHEN consulta composta só de stopwords THEN system SHALL ter zero tokens de conteúdo → curta → elegível para expansão (se habilitado) ou verbatim (se desabilitado).
- WHEN a reescrita altera/omite o termo de medicamento da consulta THEN system SHALL sujeitar a consulta efetiva à guarda lexical como de costume; se a guarda esvaziar o contexto, vira cold start (comportamento seguro, nunca contorna a guarda).
- WHEN híbrido RAG-02 está ativo THEN system SHALL usar a consulta efetiva (possivelmente reescrita) nos caminhos denso e esparso.
- WHEN falha/timeout na chamada ao LLM THEN system SHALL degradar para a consulta original sem propagar exceção (AC5).

---

## Requirement Traceability

| Requirement ID | Story | Phase | Status |
|----------------|-------|-------|--------|
| RQ-03-01 | P1: Expansão + P1: Heurística | Design | Pending |
| RQ-03-02 | P1: Expansão | Design | Pending |
| RQ-03-03 | P1: Expansão | Design | Pending |
| RQ-03-04 | P1: Expansão | Design | Pending |
| RQ-03-05 | P1: Expansão | Design | Pending |
| RQ-03-06 | P1: Expansão | Design | Pending |
| RQ-03-07 | P1: Expansão | Design | Pending |
| RQ-03-08 | P1: Expansão | Design | Pending |
| RQ-03-09 | P1: Expansão + P1: Heurística | Design | Pending |
| RQ-03-10 | P1: Expansão | Design | Pending |
| RQ-03-11 | P1: Expansão | Design | Pending |

**ID format:** `RQ-03-[NUMBER]` — prefixo da sub-feature RAG-03.
**Coverage:** 11 total, mapeados 1:1 aos ACs aprovados (AC1–AC11). ✅

---

## Data Model / Settings Changes

Sem modelos persistidos novos (nenhuma migração; contrato da API inalterado). Novos campos de configuração no bloco **Retrieval** de `src/medasist/config.py` (precedente: `retrieval_rerank_*`, `retrieval_hybrid_*`), todos com prefixo `retrieval_query_rewrite_`:

| Field | Type | Default | Env var | Constraint | Rationale |
|-------|------|---------|---------|------------|-----------|
| `retrieval_query_rewrite_enabled` | `bool` | `False` | `RETRIEVAL_QUERY_REWRITE_ENABLED` | — | Flag off por padrão (identidade, precedente RAG-01/02) |
| `retrieval_query_rewrite_min_length` | `int` | `3` | `RETRIEVAL_QUERY_REWRITE_MIN_LENGTH` | `gt=0` | Nº de tokens de conteúdo abaixo do qual a query é curta (heurística estrita `<`) |
| `retrieval_query_rewrite_model` | `str` | `""` | `RETRIEVAL_QUERY_REWRITE_MODEL` | vazio resolve para `lm_studio_llm_model` | Modelo de reescrita; default segue o modelo principal (padrão `eval_llm_model`) |
| `retrieval_query_rewrite_temperature` | `float` | `0.0` | `RETRIEVAL_QUERY_REWRITE_TEMPERATURE` | `ge=0.0, le=2.0` | Baixa para determinismo (Q3) |
| `retrieval_query_rewrite_max_tokens` | `int` | `128` | `RETRIEVAL_QUERY_REWRITE_MAX_TOKENS` | `gt=0` | Limite de geração do `ChatOpenAI` de reescrita |
| `retrieval_query_rewrite_max_output` | `int` | `200` | `RETRIEVAL_QUERY_REWRITE_MAX_OUTPUT` | `gt=0` | Limite de caracteres da query reescrita aceita (AC9) |

- `retrieval_query_rewrite_model=""` resolve para `lm_studio_llm_model` via `model_validator` existente `_resolve_eval_models` (estendido) ou novo validator — sem duplicar o valor do modelo no código.
- Nenhum dado de paciente; nenhuma coluna/schema novo em ChromaDB.

## Process / Background Flow

**Happy path (rewrite habilitado + consulta curta):**
1. `retrieve(query, stores, settings)` retorna `[]` cedo se `stores` vazio (AC7) — antes de qualquer reescrita.
2. Hook após o early return de stores vazio e antes de `k = settings.retrieval_top_k`: `effective_query = rewrite_query(query, settings)`.
3. `rewrite_query`: flag off → identidade (AC3); não-curta → identidade sem LLM (AC4); curta → chama o LLM de reescrita (prompt de expansão, temperatura baixa) → valida/trunca → retorna a expansão ou a original em falha/saída inválida (AC5/AC6/AC9).
4. `retrieve` usa `effective_query` para `similarity_search_with_score` (denso), para o caminho esparso (se híbrido) e para `_lexical_relevance_guard` e rerank.
5. A pergunta **original** continua sendo o `query` do chamador — `run_query` a passa ao prompt (chain.py:146) e a `build_citations` (chain.py:129) (AC2).
6. Cold start: se `effective_query` não recupera nada acima do threshold → `[]` (AC8); `run_query` devolve `cold_start_message` sem chamar o LLM de geração.

**Failure path — LLM indisponível/falha/timeout:** `_expand` lança → `logger.exception("...")` + retorna a consulta original; nenhuma exceção propaga para `retrieve` (AC5).

**Failure path — saída inválida/vazia/whitespace/preâmbulo:** após strip, vazia ou sem caracteres de palavra → retorna a original (AC6); excede `max_output` → trunca (AC9).

## API Changes

Nenhuma mudança externa. `POST /query` (QueryRequest/QueryResponse), `POST /ingest` e `GET /health` inalterados. Mudança interna apenas dentro de `retrieve()` (funil único) — invariante AD-011 preservado. Log do `query.py:52` continua registrando a pergunta original (inalterado).

## Frontend Changes

Nenhuma. UI (Streamlit) inalterada — reescrita controlada por configuração de backend.

## Tests Required

**Unit:**
- `tests/retrieval/test_query_rewrite.py` (new): heurística `_is_short` (curta vs não-curta no limite exato `min_length`, só-stopwords), identidade flag off (RQ-03-03), identidade não-curta sem LLM (RQ-03-04), degradação em falha do `ChatOpenAI` mockado no local real `medasist.retrieval.query_rewrite.ChatOpenAI` (RQ-03-05), saída vazia/whitespace/inválida → original (RQ-03-06), truncamento por `max_output` e prompt que proíbe preâmbulo (RQ-03-09), resolução do modelo (RQ-03-01).
- `tests/config/test_config.py` (modify): `TestSettingsQueryRewrite` — defaults (`enabled=False`, `min_length=3`, `max_tokens=128`, `max_output=200`, modelo resolve para `lm_studio_llm_model`, temp=0.0), override por env (RQ-03-11), constraints inválidas (`min_length=0`, `temperature=-0.1/2.1`, `max_tokens=0`, `max_output=0` → `ValidationError`) (RQ-03-11).

**Integration:**
- `tests/retrieval/test_retriever.py` (modify): `rewrite_query` mockado no local real (`medasist.retrieval.retriever.rewrite_query`) — retrieve usa a consulta expandida na busca (RQ-03-01/02); `stores` vazio → reescrita não chamada (RQ-03-07); expandida recupera nada → `[]` cold start (RQ-03-08); flag off → identidade sem chamada ao LLM (RQ-03-03).

**Aceite:**
- `tests/acceptance/test_rag03_query_rewrite.py` (new, espelhando `test_rag02_hybrid_search.py`): fluxo completo com flag on/off via `retrieve()`/`run_query()`/`build_citations()`, LLM mockado (nunca rede), ChromaDB real em `tmp_path`, `_FakeEmbeddings`/`_DivergentEmbeddings` — cobrindo os ACs numerados (RQ-03-01..RQ-03-11), inclusive o invariante de avaliação via `_collect_rows` (RQ-03-10).

**Testes existentes que devem continuar verdes:** `tests/retrieval/test_retriever.py`, `tests/retrieval/test_reranker.py`, `tests/acceptance/test_rag01_reranking.py`, `tests/acceptance/test_rag02_hybrid_search.py`, `tests/config/test_config.py`, `tests/evaluation/test_metrics.py` — flag off por padrão garante identidade.

## Files That Will Change

| File | Change type | Why |
|------|-------------|-----|
| `src/medasist/retrieval/query_rewrite.py` | New | Módulo com heurística `_is_short`, `_expand` (ChatOpenAI lazy) e público `rewrite_query(query, settings) -> str`; template de prompt module-level; sem import circular com retriever |
| `src/medasist/retrieval/retriever.py` | Modify | Hook `effective_query = rewrite_query(query, settings)` em `retrieve()` (após early return de stores vazio, antes de `k`); usar `effective_query` na busca/guarda/rerank; campo aditivo `rewritten` em `_log_retrieve_metric` |
| `src/medasist/config.py` | Modify | Campos `retrieval_query_rewrite_*` no bloco Retrieval + resolução do modelo vazio |
| `.env.example` | Modify | Documentar as novas env vars com defaults e constraints (RQ-03-11) |
| `tests/config/test_config.py` | Modify | `TestSettingsQueryRewrite` (defaults/override/constraints) |
| `tests/retrieval/test_query_rewrite.py` | New | Unit do módulo de reescrita (heurística, degradação, prompt) |
| `tests/retrieval/test_retriever.py` | Modify | Integração do hook no funil (mock de `rewrite_query`) |
| `tests/acceptance/test_rag03_query_rewrite.py` | New | Aceite end-to-end dos ACs numerados |

*(Nenhuma mudança em requirements — `ChatOpenAI` já disponível via langchain-openai.)*

## Risks

- **Saída não-confiável do LLM (injeção de prompt no texto reescrito):** a query reescrita é texto de LLM e pode conter instruções. **Mitigação principal:** a query reescrita é usada **somente para retrieval (similarity search)** — nunca é interpolada no prompt de geração (chain.py passa a pergunta original, AC2). Adicionalmente: comprimento limitado por `max_output` (AC9), prompt proíbe preâmbulo/cochicho (AC9) e saída inválida cai para a original (AC6).
- **Cold start contornado:** risco de a reescrita fazer a busca "sempre retornar algo". **Mitigação:** cold start decidido pós-reescrita sobre a consulta efetiva, com o mesmo threshold L2 e guarda lexical; reescrita jamais força resultado (AC7/AC8).
- **Custo/latência extra:** chamada extra ao LLM em toda consulta curta quando habilitado. **Mitigação:** flag off por padrão; apenas consultas curtas; temperatura baixa e `max_tokens` pequeno (128); falha degrada para identidade.
- **Falso-negativo da guarda lexical pós-reescrita:** a expansão pode omitir o termo de medicamento → guarda esvazia o contexto → cold start (comportamento seguro, aceito; mesmo trade-off documentado de AD-012).
- **Testes existentes com `MagicMock(spec=Chroma)` sem stores reais:** risco de quebra — mitigado por flag off default + import lazy do módulo (RQ-03-03; mesma estratégia RAG-01/02).
- **Import circular retriever ↔ query_rewrite:** retriever importa `rewrite_query`; query_rewrite NÃO importa de retriever — define seu próprio `_TOKEN_RE` local (mesmo padrão) e usa `settings.retrieval_stopwords`.
- **Determinismo:** LLM de reescrita com `temperature=0.0` para reduzir variação (Q3); falha sempre cai para identidade.
- **Divergência do caminho de avaliação (AD-011):** qualquer lógica de reescrita fora de `retrieve()` quebraria o eval — mitigado por RQ-03-10 (reescrita só dentro de `retrieve()`).

## Decisões Resolvidas (Q1–Q3 — vinculantes)

| # | Pergunta | Decisão (vinculante) |
|---|----------|----------------------|
| Q1 | Heurística de comprimento | **Token de conteúdo com stopwords filtradas, limite estrito `<`:** query é curta SE `len(content_tokens) < retrieval_query_rewrite_min_length` (default 3). `content_tokens` = tokens de `_TOKEN_RE` (padrão local) menos `retrieval_stopwords`. Exatamente `min_length` tokens → NÃO curta (verbatim, AC4). |
| Q2 | Limite de comprimento da reescrita | **Truncar** (não rejeitar) após `strip` em `retrieval_query_rewrite_max_output` caracteres (AC9). Saída vazia/whitespace/sem caracteres de palavra → usar a original (AC6). |
| Q3 | Temperatura do LLM de reescrita | **`retrieval_query_rewrite_temperature=0.0`** (determinismo). Modelo `retrieval_query_rewrite_model` vazio resolve para `lm_studio_llm_model` (padrão `eval_llm_model`). |

---

## Open Questions

None. (Q1–Q3 resolvidas e vinculantes; registradas na tabela acima.)

---

## Success Criteria

- [ ] Com `retrieval_query_rewrite_enabled=False` (default), a suíte existente passa integralmente e `retrieve()` é idêntico ao atual — sem nenhuma chamada ao LLM.
- [ ] Com flag ativa e query curta, o LLM (mockado) expande e `retrieve`/`run_query` usam a expansão na busca, enquanto o prompt de geração e as citações referenciam a pergunta original (AC1/AC2).
- [ ] Falha, timeout ou saída inválida do LLM degradam para a consulta original com erro logado, sem propagar exceção (AC5/AC6).
- [ ] `stores` vazio e expansão-sem-hit retornam `[]` cold start, sem chamar o LLM de reescrita nem contornar a invariante (AC7/AC8).
- [ ] `tests/retrieval/test_query_rewrite.py`, `tests/acceptance/test_rag03_query_rewrite.py` e os demais testes novos passam; cobertura ≥ 80% no gate full.
- [ ] Avaliação RAG (`_collect_rows`) exercita o mesmo caminho de reescrita da API (AD-011) — nenhuma lógica fora de `retrieve()` (AC10).
- [ ] `.env.example` documenta todas as novas settings com defaults e constraints (AC11).
