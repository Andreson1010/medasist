# RAG-03 — Decomposição de perguntas multi-parte — Technical Spec

**Path:** `.specs/features/rag-03-multipart/spec.md`
**TLC scope:** large
**Based on story:** "usuário do MedAssist quer pergunta composta dividida em sub-perguntas, cada uma com retrieval próprio e citações, respostas re-combinadas em resposta final única" (+ P2 paridade no streaming e avaliação).
**Status:** Awaiting human approval

---

## Problem Statement

Perguntas compostas ("Qual a dose de dipirona e posso tomar com álcool?", "Quais os efeitos colaterais do Alphazol e a interação com Betazol?") misturam múltiplas intenções de busca numa única query. O retrieval denso por embeddings trata a query como uma unidade, recuperando um único contexto que raramente cobre bem as duas partes — o LLM então responde parcialmente ou alucina. RAG-03 adiciona um passo opcional de **decomposição multi-parte**: quando habilitado, uma pergunta composta é dividida em sub-perguntas, cada uma passa pelo funil independente de `retrieve()` (com o mesmo threshold L2, guarda lexical e, se ativo, reescrita de consulta curta por sub-pergunta), cada sub-resposta é validada e suas citações re-numeradas num espaço 1-based único com os marcadores `[N]` remapeados, e tudo é re-combinado numa resposta final única. A feature é **desabilitada por padrão** e degrada graciosamente (identidade total) em qualquer falha, sem nunca violar cold start, sem fabricar conteúdo e sem alterar o contrato flat do `QueryResponse` além de um campo aditivo opcional.

## Goals

- [ ] Permitir que, quando `retrieval_decompose_enabled=True`, uma pergunta composta seja dividida em sub-perguntas, cada uma passando pelo funil `retrieve()` (mesmo threshold L2 + guarda lexical + reescrita curta por sub-pergunta), com respostas re-combinadas numa resposta única com citações re-numeradas 1-based e `[N]` remapeados (AC2/AC3/AC6).
- [ ] Garantir cold start **parcial**: todas-miss → cold start total; pelo menos uma sub-pergunta hit → responde os hits e registra os misses no campo aditivo `unanswered_sub_questions`, **nunca fabricando** conteúdo (AC9/AC10).
- [ ] Preservar identidade total quando `retrieval_decompose_enabled=False` (default): sem chamada ao LLM de split, sem divisão, sem merge — suíte existente verde (AC1).
- [ ] Degradar graciosamente (identidade, erro logado) em falha/timeout/saída malformada/0 sub-perguntas do LLM de split, sem propagar exceção (AC7/AC8).
- [ ] Limitar custo/latência com `retrieval_decompose_max_sub_questions` (default 5) e um gate heurístico determinístico para só acionar o split em perguntas realmente compostas (AC2/AC5).
- [ ] Manter paridade P2: `stream_answer` e `_collect_rows` exercitam o **mesmo caminho de decomposição** do fluxo síncrono `run_query` (AC12/AC14).
- [ ] Sub-perguntas usadas **apenas** para retrieval/geração própria — nunca interpoladas no prompt de geração além disso (AC13).

## Out of Scope

| Feature | Reason |
|---------|--------|
| Alterar o contrato `POST /query` em campos existentes | Contrato permanece FLAT; só adiciona campo aditivo opcional `unanswered_sub_questions` (decisão 2) |
| Extender o schema SSE do streaming para expor `unanswered_sub_questions` | P2 exige o mesmo **caminho** de decomposição; o campo aditivo é do `QueryResponse` flat (decisão Q1/Q2) |
| Respostas parciais fabricadas para sub-perguntas miss | Regra de segurança: nunca fabricar conteúdo; misses só registradas (AC10) |
| Reordenação/mesclagem semântica das sub-respostas (re-ranqueamento inter-sub) | Merge é concatenação na ordem das sub-perguntas (determinístico) |
| Multiturno / histórico de conversa | Fora do escopo |
| Cache de decomposições (por query/fingerprint) | Sem evidência de necessidade; gate heurístico já limita custo |
| Alterar `retrieve()`/funil existente (L2, threshold, dedup, guarda, rerank, híbrido) | Cada sub-pergunta reusa o funil existente verbatim |
| Novas dependências de runtime | Usa `ChatOpenAI` já presente (langchain-openai) |
| Decomposição sem gate heurístico (sempre chamar o split LLM) | Custo: o split só roda quando `_is_compound` é verdadeiro (Q4) |

---

## User Stories

### P1: Decomposição multi-parte no fluxo síncrono ⭐ MVP

**User Story**: Como profissional de saúde que digita perguntas compostas ("Qual a dose de dipirona e posso tomar com álcool?"), quero que, quando a decomposição estiver habilitada, o sistema divida minha pergunta em sub-perguntas, recupere e responda cada uma com suas próprias citações e re-combine tudo numa resposta única coerente, para obter uma resposta completa e com fontes por parte, sem que nenhuma parte seja perdida ou inventada.

**Why P1**: É o caso de uso central da story aprovada e o motivo da sub-feature.

**Acceptance Criteria**:

1. WHEN `retrieval_decompose_enabled=False` (default) THEN system SHALL usar a pergunta inalterada, SEM divisão, SEM chamada ao LLM de split e SEM merge — comportamento idêntico ao atual, suíte existente verde. *(MP-01)*
2. WHEN `retrieval_decompose_enabled=True` E a pergunta é composta (critério determinístico — decisão Q4) THEN system SHALL dividir em 2+ sub-perguntas via o LLM de split e EACH sub-pergunta SHALL passar pelo funil `retrieve()` com o MESMO threshold L2 e a MESMA guarda lexical. *(MP-02)*
3. WHEN todas as sub-perguntas têm hit THEN system SHALL validar cada sub-resposta com `validate_citations` (sem marcador `[N]` órfão), re-numerar as citações num espaço 1-based único e remapear os `[N]` correspondentes no texto merged. *(MP-03)*
4. WHEN a resposta merged é montada THEN system SHALL ter ≥1 citação válida no formato `[N] <nome_doc> — Seção: <seção>, Pág. <pág>` e incluir o disclaimer médico. *(MP-04)*
5. WHEN o LLM de split retorna mais que `retrieval_decompose_max_sub_questions` (default 5) sub-perguntas THEN system SHALL processar apenas as 5 primeiras e descartar as demais. *(MP-05)*
6. WHEN uma sub-pergunta é curta E `retrieval_query_rewrite_enabled=True` THEN system SHALL passá-la pela reescrita de consulta curta (RAG-03 pré-existente) ANTES do retrieval — cada sub-pergunta passa pelo funil independente, incluindo guarda lexical própria. *(MP-06)*
7. WHEN o LLM de split falha/timeout/retorna saída malformada/0 sub-perguntas THEN system SHALL usar a pergunta original (identidade), logar com `logger.exception` e NUNCA propagar o erro. *(MP-07)*
8. WHEN o LLM de split retorna exatamente 1 sub-pergunta THEN system SHALL processar como pergunta única, SEM re-numeração indevida (identidade). *(MP-08)*
9. WHEN TODAS as sub-perguntas são miss (retrieval vazio ou sem citação válida) THEN system SHALL retornar cold start total (mensagem fixa, zero geração). *(MP-09)*
10. WHEN ALGUMAS sub-perguntas são miss THEN system SHALL responder os hits e registrar as misses no campo aditivo `unanswered_sub_questions`, sem fabricar conteúdo para as misses. *(MP-10)*
11. WHEN qualquer sub-resposta falha `validate_citations` THEN system SHALL remover os marcadores órfãos ANTES da re-numeração. *(MP-11)*
12. WHEN o script de avaliação roda uma pergunta composta via `_collect_rows` THEN system SHALL exercitar o MESMO caminho de decomposição do `run_query` (invariante AD-011) — nenhuma lógica de decomposição fora do layer `retrieve`/`run_query`. *(MP-12)*
13. WHEN uma pergunta composta é decomposta THEN system SHALL usar as sub-perguntas APENAS para retrieval/geração própria — nunca interpolar a lista de sub-perguntas, a pergunta original composta ou meta de decomposição no prompt de geração além da sub-pergunta em questão. *(MP-13)*

**Independent Test**: `pytest tests/acceptance/test_rag03_multipart.py` — com `retrieval_decompose_enabled=True`, pergunta composta "Qual a dose de dipirona e posso tomar com álcool?" sobre corpus de bula com embeddings que recuperam cada parte, o LLM de split (mockado) retorna as 2 sub-perguntas, cada uma passa por `retrieve()` com reescrita/guarda, e a resposta merged tem citações re-numeradas 1-based e `[N]` remapeados; com flag off, `tests/retrieval/test_retriever.py` e `tests/generation/test_chain.py` passam integralmente (identidade).

---

### P2: Paridade no streaming e avaliação

**User Story**: Como mantenedor, quero que `stream_answer` e `_collect_rows` percorram exatamente o mesmo caminho de decomposição/merge do `run_query`, para que o comportamento sincrono, o streaming e a avaliação não divirjam quando a decomposição é ativada.

**Why P2**: P2 da story aprovada — sem paridade, ativar a decomposição quebraria a equivalência entre os três consumidores do pipeline.

**Acceptance Criteria**:

1. WHEN `stream_answer` é chamado com `retrieval_decompose_enabled=True` E pergunta composta THEN system SHALL decompor e, para cada sub-pergunta, gerar deltas pelo mesmo funil (retrieval + cold start + citações) do `run_query`, retornando as citações merged re-numeradas e a flag de cold start conforme a política parcial. *(MP-14)*
2. WHEN `_collect_rows` processa uma pergunta composta THEN system SHALL passar por `run_query` (que aplica a decomposição) — o caminho de avaliação nunca contorna a decomposição. *(MP-12)*

**Independent Test**: `pytest tests/generation/test_chain.py::TestStreamDecompose` e `tests/evaluation/test_metrics.py` — `stream_answer` com pergunta composta produz deltas cuja concatenação é a resposta merged e retorna citações re-numeradas; `_collect_rows` sobre pergunta composta usa o `run_query` real com o split mockado.

---

## Edge Cases

- WHEN flag off THEN system SHALL identidade total: `decompose_query` retorna `[query]` e `run_query`/`stream_answer` seguem o caminho único atual, sem chamada ao LLM de split.
- WHEN pergunta não-composta (heurística Q4) E flag on THEN system SHALL NÃO chamar o LLM de split (gate determinístico) — pergunta segue única.
- WHEN pergunta composta mas LLM de split retorna 1 sub-pergunta THEN system SHALL identidade (pergunta única, sem re-numeração).
- WHEN split LLM retorna 7 sub-perguntas THEN system SHALL processar as 5 primeiras (cap) e descartar as demais.
- WHEN todas-miss THEN cold start total: `is_cold_start=True`, `answer=cold_start_message`, `citations=[]`, `unanswered_sub_questions=[]`.
- WHEN algumas-miss THEN `is_cold_start=False`, resposta = concatenação dos hits com `[N]` remapeados, citações = merged re-numeradas, `unanswered_sub_questions=[texto das misses]`.
- WHEN uma sub-pergunta hit no retrieval mas sua geração não produz citação válida THEN system SHALL tratá-la como miss (não entra no merged; registrada em `unanswered_sub_questions`).
- WHEN a pergunta é curta (total de tokens < `retrieval_decompose_min_tokens`, default 4) THEN `_is_compound` retorna `False` sem chamar o split — gate determinístico de custo (Q4).
- WHEN a pergunta tem o conector `e` (que é stopword) THEN `_is_compound` detecta o conector no texto bruto (pré-remoção de stopwords), pois `e` coordena cláusulas mesmo sendo stopword (Q4).
- WHEN sub-pergunta curta + rewrite on THEN a reescrita roda dentro do `retrieve()` daquela sub-pergunta (funnel independente, decisão 4).
- WHEN a sub-pergunta decomposta omite o termo de medicamento na reescrita THEN a guarda lexical daquela sub-pergunta esvazia o contexto → miss (comportamento seguro, nunca contorna a guarda).
- WHEN `stores` vazio THEN system SHALL retornar `[]`/cold start ANTES de qualquer split — o LLM de split NUNCA é chamado.
- WHEN o merged fica com 0 citações válidas (nenhuma sub-pergunta com citação válida) THEN system SHALL cold start total (nunca resposta sem fonte — regra de segurança médica).

---

## Requirement Traceability

| Requirement ID | Story | Phase | Status |
|----------------|-------|-------|--------|
| MP-01 | P1: Decomposição | Design | Pending |
| MP-02 | P1: Decomposição | Design | Pending |
| MP-03 | P1: Decomposição | Design | Pending |
| MP-04 | P1: Decomposição | Design | Pending |
| MP-05 | P1: Decomposição | Design | Pending |
| MP-06 | P1: Decomposição | Design | Pending |
| MP-07 | P1: Decomposição | Design | Pending |
| MP-08 | P1: Decomposição | Design | Pending |
| MP-09 | P1: Decomposição | Design | Pending |
| MP-10 | P1: Decomposição | Design | Pending |
| MP-11 | P1: Decomposição | Design | Pending |
| MP-12 | P1: Decomposição + P2: Paridade | Design | Pending |
| MP-13 | P1: Decomposição | Design | Pending |
| MP-14 | P2: Paridade | Design | Pending |

**ID format:** `MP-[NUMBER]` — prefixo da sub-feature multi-parte.
**Coverage:** 14 total, mapeados 1:1 aos ACs aprovados (AC1–AC13 + P2). ✅

---

## Data Model / Settings Changes

Sem modelos persistidos novos (nenhuma migração; contrato `QueryRequest` inalterado). `QueryResponse` ganha **um campo aditivo opcional** (default vazio — retrocompatível, decisão Q1). Novos campos de configuração no bloco **Retrieval** de `src/medasist/config.py` (precedente: `retrieval_query_rewrite_*`, `retrieval_rerank_*`), todos com prefixo `retrieval_decompose_`:

| Field | Type | Default | Env var | Constraint | Rationale |
|-------|------|---------|---------|------------|-----------|
| `retrieval_decompose_enabled` | `bool` | `False` | `RETRIEVAL_DECOMPOSE_ENABLED` | — | Flag off por padrão (identidade; precedente RAG-01/02/03/05) |
| `retrieval_decompose_max_sub_questions` | `int` | `5` | `RETRIEVAL_DECOMPOSE_MAX_SUB_QUESTIONS` | `gt=0` | Cap de sub-perguntas processadas (decisão 5/Q2) |
| `retrieval_decompose_model` | `str` | `""` | `RETRIEVAL_DECOMPOSE_MODEL` | vazio resolve para `lm_studio_llm_model` | Modelo do split; default segue o principal (padrão `eval_llm_model`/`retrieval_query_rewrite_model`) |
| `retrieval_decompose_temperature` | `float` | `0.0` | `RETRIEVAL_DECOMPOSE_TEMPERATURE` | `ge=0.0, le=2.0` | Baixa para determinismo (Q2) |
| `retrieval_decompose_max_tokens` | `int` | `256` | `RETRIEVAL_DECOMPOSE_MAX_TOKENS` | `gt=0` | Limite de geração do LLM de split |
| `retrieval_decompose_min_tokens` | `int` | `4` | `RETRIEVAL_DECOMPOSE_MIN_TOKENS` | `gt=0` | Gate da heurística `_is_compound` — mínimo de TOKENS TOTAIS (Q4) |

- `retrieval_decompose_model=""` resolve para `lm_studio_llm_model` via `model_validator` existente `_resolve_eval_models` (estendido — mesmo padrão de `retrieval_query_rewrite_model`).
- `GenerationResult` (chain.py) ganha campo aditivo `unanswered_sub_questions: list[str] = field(default_factory=list)`.
- `QueryResponse` (schemas.py) ganha campo aditivo `unanswered_sub_questions: list[str] = Field(default_factory=list)`; `from_result` mapeia de `GenerationResult`.
- Nenhum dado de paciente; nenhuma coluna/schema novo em ChromaDB.

## Process / Background Flow

**Happy path (decomposição habilitada + pergunta composta):**
1. `run_query(question, ...)` chama `decompose_query(question, settings)`.
2. `decompose_query`: flag off → `[question]` (MP-01); `_is_compound` falso → `[question]` sem LLM (gate Q4); composto → chama o LLM de split, parseia em sub-perguntas, aplica cap `max_sub_questions` (MP-05); falha/malformado/0/1 → `[question]` com `logger.exception` quando falha (MP-07/08).
3. Se `len == 1` → `_run_single(question)` — exatamente o corpo atual de `run_query` (identidade byte-identical quando flag off).
4. Se `len > 1` → para cada sub-pergunta `_run_single(sub)`: funil `retrieve()` com reescrita curta (se ativo) + guarda lexical + threshold L2, geração com o prompt do perfil e `validate_citations` (MP-02/03/06/11). Cada sub com `is_cold_start`/citações próprias.
5. `_merge_sub_results`: cada sub com citação válida vira uma parte do merged — `[N]` remapeados por offset acumulado e citações re-numeradas 1-based; subs sem citação válida (miss) registradas em `unanswered_sub_questions` (MP-03/10/11).
6. Se nenhuma sub com citação válida → cold start total (MP-09); senão → `GenerationResult(answer=merged, citations=renumeradas, is_cold_start=False, unanswered_sub_questions=misses)` com disclaimer (MP-04).

**Failure path — split LLM indisponível/falha/timeout:** `_split` lança → `logger.exception` + retorna `[question]`; nenhuma exceção propaga (MP-07).

**Failure path — saída malformada/0/1 sub-perguntas:** parse devolve lista vazia ou de 1 → identidade `[question]` (MP-07/08).

**Failure path — algumas-miss / todas-miss:** política parcial (MP-09/10) descrita acima; nunca fabrica conteúdo.

## API Changes

Nenhum endpoint novo nem alteração de rota. `POST /query`, `POST /query/stream`, `POST /ingest` e `GET /health` inalterados. Mudança interna: `QueryResponse` ganha campo aditivo `unanswered_sub_questions` (default `[]` — retrocompatível; decisão Q1). Mudança interna apenas no layer `generation`/`retrieval` — invariante AD-011 preservado (MP-12). O streaming reusa o mesmo caminho de decomposição (MP-14); o campo aditivo é exposto apenas no `QueryResponse` flat (decisão Q1/Q2).

## Frontend Changes

Nenhuma obrigatória. A UI (Streamlit) não precisa mudar para a feature funcionar; `QueryResponse.unanswered_sub_questions` fica disponível para futura renderização (ex: aviso de partes não respondidas), mas fora do escopo deste spec.

## Tests Required

**Unit:**
- `tests/retrieval/test_decompose.py` (new): `_is_compound` (composta vs não-composta no limite `min_tokens`, conectores `e`/`ou`/`e/ou` no texto bruto + vírgula, exemplos positivos/negativos do Q4 — P1 "Qual a dose de dipirona e posso tomar com álcool?" composta; "Alphazol", "Alphazol causa sonolência intensa", "Alphazol ou Betazol" (min=4), "qual a dose para" não-composta), `decompose_query` — identidade flag off (MP-01), não-composta sem LLM (MP-02/gate), LLM falha/timeout → identidade + `logger.exception` sem propagar (MP-07), saída malformada/vazia → identidade (MP-07), cap `max_sub_questions` (MP-05), 1 sub-pergunta → identidade (MP-08), 2+ sub-perguntas parseadas (MP-02), resolução do modelo (MP-02).
- `tests/generation/test_chain.py` (modify): `_run_single`/`run_query` — identidade flag off (MP-01), decomposição 2 subs com merge + re-numeração (MP-03/04), todas-miss cold start total (MP-09), algumas-miss com `unanswered_sub_questions` (MP-10), sub sem citação válida tratada como miss (MP-11), `_is_compound`/gate não chama split (MP-02), reescrita por sub-pergunta (MP-06); `stream_answer` — deltas concatenados = merged + citações re-numeradas + cold start parcial (MP-14).
- `tests/config/test_config.py` (modify): `TestSettingsDecompose` — defaults (`enabled=False`, `max_sub_questions=5`, `min_tokens=4`, modelo resolve para `lm_studio_llm_model`, `temperature=0.0`, `max_tokens=256`), override por env (MP-01/02/05), constraints inválidas (`max_sub_questions=0`, `min_tokens=0`, `temperature=-0.1/2.1`, `max_tokens=0` → `ValidationError`).
- `tests/generation/test_citations.py` (modify): `remap_answer` — reescrita de `[N]` via mapa `{índice original: índice global}`, remoção de fora-do-mapa, sem alterar texto sem marcadores.

**Integration (API):**
- `tests/api/test_query.py` (modify): `QueryResponse.unanswered_sub_questions` serialização (default `[]`; preenchido quando decomposição parcial) — retrocompatibilidade com respostas existentes (MP-10).
- `tests/api/test_query_logging.py` (modify): log composto do `run_query` (nº subs, hits, misses) sem vazar dados.
- `tests/api/test_sse_helpers.py` (modify): nenhuma mudança de schema SSE; mantém verde.

**Avaliação:**
- `tests/evaluation/test_metrics.py` (modify): `_collect_rows` sobre pergunta composta usa o `run_query` real (decomposição ativa) — invariante AD-011 (MP-12).

**Aceite:**
- `tests/acceptance/test_rag03_multipart.py` (new, espelhando `test_rag03_query_rewrite.py`): fluxo completo flag on/off via `retrieve()`/`run_query()`/`stream_answer()`/`_collect_rows()`, LLM de split e de geração mockados (nunca rede), ChromaDB real em `tmp_path`, `_FakeEmbeddings`/`_DivergentEmbeddings` — cobrindo MP-01..MP-14.

**Testes existentes que devem continuar verdes:** `tests/retrieval/test_retriever.py`, `tests/retrieval/test_query_rewrite.py`, `tests/retrieval/test_reranker.py`, `tests/generation/test_chain.py`, `tests/generation/test_citations.py`, `tests/api/test_query.py`, `tests/api/test_sse_helpers.py`, `tests/config/test_config.py`, `tests/evaluation/test_metrics.py`, `tests/acceptance/test_rag03_query_rewrite.py` — flag off por padrão garante identidade.

## Files That Will Change

| File | Change type | Why |
|------|-------------|-----|
| `src/medasist/retrieval/decompose.py` | New | Módulo com `_is_compound` (heurística determinística), `_split` (ChatOpenAI lazy) e público `decompose_query(query, settings) -> list[str]`; `_TOKEN_RE` local (sem import circular); template de prompt module-level |
| `src/medasist/generation/chain.py` | Modify | Refatorar `run_query` → `_run_single` (corpo atual) + hook `decompose_query` + `_merge_sub_results`; `GenerationResult.unanswered_sub_questions`; `stream_answer` com paridade de decomposição |
| `src/medasist/generation/citations.py` | Modify | Helper `remap_answer(answer, index_map) -> str` (reescreve `[N]` via mapa sequencial; fora-do-mapa removido) |
| `src/medasist/api/schemas.py` | Modify | `QueryResponse.unanswered_sub_questions: list[str] = Field(default_factory=list)` + mapeamento em `from_result` |
| `src/medasist/config.py` | Modify | Campos `retrieval_decompose_*` no bloco Retrieval + resolução do modelo vazio em `_resolve_eval_models` |
| `.env.example` | Modify | Documentar as novas env vars com defaults e constraints |
| `tests/config/test_config.py` | Modify | `TestSettingsDecompose` |
| `tests/retrieval/test_decompose.py` | New | Unit do módulo de split (heurística, cap, degradação) |
| `tests/generation/test_chain.py` | Modify | Unit de `run_query`/`stream_answer` decomposição + merge |
| `tests/generation/test_citations.py` | Modify | Unit de `remap_answer` |
| `tests/api/test_query.py` | Modify | Serialização de `unanswered_sub_questions` |
| `tests/api/test_query_logging.py` | Modify | Log composto do run_query |
| `tests/api/test_sse_helpers.py` | Modify | Mantém verde (sem mudança de schema) |
| `tests/evaluation/test_metrics.py` | Modify | `_collect_rows` exercita decomposição (AD-011) |
| `tests/acceptance/test_rag03_multipart.py` | New | Aceite end-to-end dos ACs numerados |

*(Nenhuma mudança em requirements — `ChatOpenAI` já disponível via langchain-openai.)*

## Risks

- **Cold start contornado / fabricação de conteúdo:** risco de o merge preencher sub-perguntas miss com texto inventado ou de retornar resposta sem fonte. **Mitigação:** política parcial estrita (MP-09/10) — hits entram no merged, misses só registradas em `unanswered_sub_questions`; merged exige ≥1 citação válida senão cold start total (MP-04/09); `validate_citations` por sub (MP-11).
- **Re-numeração incorreta de citações:** risco de `[N]` do merged apontar para a fonte errada. **Mitigação:** cada sub valida citações primeiro (remove órfãos, MP-11), depois remapeia por offset acumulado e re-numera 1-based (MP-03); helper `remap_answer` testado isoladamente.
- **Custo/latência:** N sub-perguntas × (retrieval+geração) + chamada do split. **Mitigação:** flag off por padrão; gate `_is_compound` evita o split em não-compostas (Q4); cap `max_sub_questions=5` (MP-05); temperatura baixa e `max_tokens=256`.
- **Saída não-confiável do LLM de split:** sub-perguntas podem ser malformadas/injetar instruções. **Mitigação:** sub-perguntas usadas apenas para retrieval/geração própria, nunca interpoladas além disso (MP-13); falha/malformado/0/1 → identidade (MP-07/08); cap de comprimento via parse robusto (strip/filtra vazias).
- **Divergência do caminho de avaliação (AD-011):** decomposição fora de `run_query` quebraria o eval. **Mitigação:** decomposição orquestrada no layer `generation` (chamada por `run_query`/`stream_answer`), `_collect_rows` chama `run_query` real (MP-12).
- **Identidade flag off (byte-identical):** refatoração de `run_query` → `_run_single` pode alterar comportamento. **Mitigação:** `_run_single` é o corpo atual extraído verbatim; flag off → `decompose_query` retorna `[question]` → `_run_single(question)` idêntico (MP-01); suíte existente verde como gate.
- **Import circular retriever ↔ decompose:** `chain.py` importa `decompose_query`; `decompose.py` NÃO importa de retriever/chain — define `_TOKEN_RE` local e usa `settings` (padrão `query_rewrite.py`).
- **Paridade streaming (P2):** `stream_answer` deve produzir o mesmo merged que `run_query`. **Mitigação:** camada de merge compartilhada (`_merge_sub_results`/`remap_answer`) usada por ambos; deltas acumulados e validados no fim (MP-14).
- **Testes existentes com `MagicMock(spec=Chroma)` sem stores reais:** risco de quebra — mitigado por flag off default + import lazy do módulo de split (MP-01; mesma estratégia RAG-01/02/03).

## Decisões Resolvidas (Q1–Q4 — vinculantes)

| # | Pergunta | Decisão (vinculante) |
|---|----------|----------------------|
| Q1 | Formato do campo aditivo de sub-perguntas não respondidas | **`unanswered_sub_questions: list[str]`** (lista dos textos das sub-perguntas que não foram respondidas), default `[]`. Aditivo em `GenerationResult` e `QueryResponse` — não altera campos existentes (contrato FLAT, decisão 2). |
| Q2 | Limite de latência/custo | **`retrieval_decompose_max_sub_questions=5`** (cap de sub-perguntas processadas; MP-05) + gate heurístico `_is_compound` (Q4) evita chamar o split LLM em não-compostas. `retrieval_decompose_temperature=0.0` e `retrieval_decompose_max_tokens=256` para determinismo/custo. |
| Q3 | Log/observabilidade | **Composto + por sub-pergunta.** Cada sub-pergunta já loga via `retrieve()`/`_run_single` (logs existentes); o `run_query`/`stream_answer` loga uma entrada composta (nº subs, hits, misses, `unanswered_sub_questions`, cold_start) — logging aditivo (não quebra testes de log existentes). |
| Q4 | Critério exato de "pergunta composta" | **Heurística determinística + confirmação do split.** `_is_compound(query, settings)` é `True` quando (a) o conjunto de TODOS os tokens da query (via `_TOKEN_RE`, **sem** remoção de stopwords) tem ≥ `retrieval_decompose_min_tokens` (default 4) itens E (b) contém um conector de coordenação (`e`, `ou`, `e/ou`) — detectado no **texto bruto, pré-remoção de stopwords** (`e` é stopword mas é conector) — OU uma vírgula `,` seguida de mais tokens de conteúdo (`_has_comma_with_content`). A decomposição só é acionada se `_is_compound` for `True` (gate determinístico, sem chamada ao LLM caso contrário) E o LLM de split retornar >1 sub-pergunta válida. |

---

## Open Questions

None. (Q1–Q4 resolvidas e vinculantes; registradas na tabela acima.)

---

## Success Criteria

- [ ] Com `retrieval_decompose_enabled=False` (default), a suíte existente passa integralmente e `run_query`/`stream_answer`/`retrieve` são idênticos ao atual — sem chamada ao LLM de split (MP-01).
- [ ] Com flag ativa e pergunta composta, o split (mockado) divide em 2+ sub-perguntas, cada uma passa pelo funil `retrieve()` (threshold L2 + guarda + reescrita curta por sub), e o merged tem citações re-numeradas 1-based com `[N]` remapeados e ≥1 citação válida + disclaimer (MP-02/03/04/06).
- [ ] Política parcial correta: todas-miss → cold start total (MP-09); algumas-miss → hits respondidos + `unanswered_sub_questions` preenchido, sem fabricar (MP-10).
- [ ] Falha/timeout/saída malformada/0/1 sub-perguntas do split degradam para identidade com `logger.exception`, sem propagar (MP-07/08).
- [ ] Cap `max_sub_questions=5` respeitado (MP-05); sub-resposta sem citação válida tratada como miss com órfãos removidos antes da re-numeração (MP-11).
- [ ] `stream_answer` e `_collect_rows` exercitam o mesmo caminho de decomposição do `run_query` (MP-12/14).
- [ ] Sub-perguntas usadas apenas para retrieval/geração própria, nunca interpoladas no prompt além disso (MP-13).
- [ ] `tests/retrieval/test_decompose.py`, `tests/acceptance/test_rag03_multipart.py` e os demais testes novos passam; cobertura ≥ 80% no gate full.
- [ ] `.env.example` documenta todas as novas settings com defaults e constraints.
