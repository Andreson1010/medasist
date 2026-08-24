# RAG-03 — Decomposição de perguntas multi-parte — Tasks

**Design:** `.specs/features/rag-03-multipart/design.md`
**Status:** Awaiting human approval

---

## Execution Plan

### Phase 1: Foundation (Sequential)

Config + env — habilita as settings de decomposição antes de qualquer código que as consuma.

```
T1
```

### Phase 2: Building blocks (Parallel OK)

Após T1, os três blocos independentes rodam em paralelo: o split layer, o helper de remapeamento e o campo aditivo do schema.

```
T1 ──→ ┌→ T2 (retrieval/decompose) ─┐
       ├→ T3 (citations.remap)      ├─→ T5
       └→ T4 (schemas field)        ┘
```

### Phase 3: Sync orchestration (Sequential)

Refatorar `run_query` → `_run_single` + hook de decomposição + `_merge_sub_results` (depende de T2 e T3).

```
T5
```

### Phase 4: Streaming parity (Sequential)

`stream_answer` com o mesmo caminho de decomposição/merge (depende de T5).

```
T6
```

### Phase 5: Eval + Acceptance (Parallel OK)

Avaliação (`_collect_rows` — depende de T5) e aceite end-to-end (depende de T5 e T6) rodam em paralelo.

```
T5 ──→ T7 (eval) ─┐
T6 ──→ T8 (aceite) ─┼─→ T9
```

### Phase 6: Final gate (Sequential)

Gate full do repositório (pytest + black + ruff + cobertura ≥ 80%).

```
T9
```

---

## Task Breakdown

### T1: Config de decomposição (settings + .env.example)

**What:** Adicionar os campos `retrieval_decompose_*` ao bloco Retrieval de `Settings`, estender `_resolve_eval_models` para resolver `retrieval_decompose_model=""` → `lm_studio_llm_model`, e documentar todas as env vars no `.env.example` (defaults + constraints). **Nota (Q4 reconciliado):** o campo `retrieval_decompose_min_content_tokens` já commitado em 437f958 deve ser **RENOMEADO** para `retrieval_decompose_min_tokens` (env var `RETRIEVAL_DECOMPOSE_MIN_TOKENS`) — o gate agora mede tokens TOTAIS, não de conteúdo.
**Where:** `src/medasist/config.py`, `.env.example`, `tests/config/test_config.py`
**Depends on:** None
**Reuses:** padrão de constraints e resolução de modelo de `retrieval_query_rewrite_*`; `TestSettingsQueryRewrite` como molde.
**Requirement:** MP-01, MP-02, MP-05, MP-07

**Done when:**
- [ ] Campos `retrieval_decompose_enabled=False`, `retrieval_decompose_max_sub_questions=5 (gt=0)`, `retrieval_decompose_model=""`, `retrieval_decompose_temperature=0.0 (ge=0,le=2)`, `retrieval_decompose_max_tokens=256 (gt=0)`, `retrieval_decompose_min_tokens=4 (gt=0)` presentes em `Settings`.
- [ ] `retrieval_decompose_model=""` resolve para `lm_studio_llm_model` via `_resolve_eval_models`.
- [ ] `TestSettingsDecompose` cobre defaults, override por env e constraints inválidas (`max_sub_questions=0`, `min_tokens=0`, `temperature=-0.1/2.1`, `max_tokens=0` → `ValidationError`).
- [ ] `.env.example` documenta as 6 env vars `RETRIEVAL_DECOMPOSE_*` com defaults/constraints (incl. `RETRIEVAL_DECOMPOSE_MIN_TOKENS`, default 4).
- [ ] Gate check passes: `pytest tests/config/test_config.py -v`
- [ ] Test count: testes novos de `TestSettingsDecompose` passam (sem deleção silenciosa).

**Tests:** unit (config layer = "none" na matrix; testes adicionados por precedente de `TestSettingsQueryRewrite`)
**Gate:** quick

**Commit:** `feat: adiciona settings de decomposição multi-parte (RAG-03)`

---

### T2: Módulo de split — `retrieval/decompose.py` [P]

**What:** Criar `src/medasist/retrieval/decompose.py` com `decompose_query(query, settings) -> list[str]`, `_is_compound` (heurística Q4) e `_split` (ChatOpenAI lazy + prompt `_DECOMPOSE_PROMPT` + parse linha a linha + cap `max_sub_questions` + degradação para `[query]`). `_TOKEN_RE` local; NÃO importa de retriever/chain.
**Where:** `src/medasist/retrieval/decompose.py`, `tests/retrieval/test_decompose.py`
**Depends on:** T1 (usa os campos `retrieval_decompose_*`)
**Reuses:** padrão de `query_rewrite.py` (ChatOpenAI lazy module-level, `_TOKEN_RE` local, prompt module-level, `logger.exception`).
**Requirement:** MP-01, MP-02, MP-05, MP-07, MP-08

**Done when:**
- [ ] `decompose_query`: flag off → `[query]` (MP-01); não-composta → `[query]` sem chamar o LLM de split (gate Q4/MP-02); composta → split; falha/timeout/malformado/0 → `[query]` com `logger.exception` sem propagar (MP-07); 1 sub → `[query]` (MP-08); >cap → trunca nas `max_sub_questions` primeiras (MP-05).
- [ ] `_is_compound`: `True` apenas quando ≥`retrieval_decompose_min_tokens` (4) TOKENS TOTAIS (via `_TOKEN_RE`, sem remoção de stopwords) E (conector `e`/`ou`/`e/ou` no texto bruto — pré-stopwords, pois `e` é stopword mas é conector — OU vírgula seguida de tokens de conteúdo via `_has_comma_with_content`) (Q4). Exemplos: "Qual a dose de dipirona e posso tomar com álcool?" (10 tok, `e` bruto) → composta; "Alphazol" (1 tok), "Alphazol causa sonolência intensa" (sem conector), "Alphazol ou Betazol" (3 tok < 4), "qual a dose para" (sem conector) → não-composta.
- [ ] `ChatOpenAI` lazy (import dentro de `_split`); mockável via `patch("medasist.retrieval.decompose.ChatOpenAI")`.
- [ ] `tests/retrieval/test_decompose.py` cobre: flag off, não-composta sem LLM, LLM falha/timeout → identidade + `logger.exception`, saída malformada/vazia → identidade, cap, 1 sub → identidade, 2+ subs parseadas, resolução do modelo.
- [ ] Gate check passes: `pytest tests/retrieval/test_decompose.py -v`
- [ ] Test count: testes novos passam (sem deleção silenciosa).

**Tests:** unit (novo módulo; precedente `test_query_rewrite.py` = unit)
**Gate:** quick

**Commit:** `feat: adiciona decomposição de perguntas multi-parte (RAG-03)`

---

### T3: Helper `remap_answer` em `generation/citations.py` [P]

**What:** Adicionar `remap_answer(answer: str, offset: int) -> str` que substitui cada marcador `[N]` por `[N+offset]`.
**Where:** `src/medasist/generation/citations.py`, `tests/generation/test_citations.py`
**Depends on:** None
**Reuses:** regex `\[(\d+)\]` já usado em `validate_citations`.
**Requirement:** MP-03, MP-11

**Done when:**
- [ ] `remap_answer` shift de `[N]` por offset; texto sem marcadores inalterado; docstring NumPy.
- [ ] Testes unit: offset 0 (sem mudança), offset >0 (shift correto), múltiplos `[N]`, sem marcadores, `[1]`→`[k+1]`.
- [ ] Gate check passes: `pytest tests/generation/test_citations.py -v`
- [ ] Test count: testes novos passam.

**Tests:** unit (citations.py = unit na matrix)
**Gate:** quick

**Commit:** `feat: adiciona remap_answer para merge de citações (RAG-03)`

---

### T4: Campo aditivo `unanswered_sub_questions` em `QueryResponse` [P]

**What:** Adicionar `unanswered_sub_questions: list[str] = Field(default_factory=list)` a `QueryResponse` e mapear de `GenerationResult` em `from_result`.
**Where:** `src/medasist/api/schemas.py`, `tests/api/test_query.py`
**Depends on:** None (mapeia de `GenerationResult.unanswered_sub_questions`, que é criado em T5 — campo já existente no dataclass a partir de T5; para não quebrar compilação, ver nota).
**Reuses:** padrão `Field(default_factory=list)` de `QueryResponse.citations`.
**Requirement:** MP-10

**Nota de compilação (merge forward):** `QueryResponse.from_result` referencia `result.unanswered_sub_questions`. Para que T4 seja testável isoladamente, T4 **também** adiciona o campo `unanswered_sub_questions: list[str] = field(default_factory=list)` ao dataclass `GenerationResult` em `chain.py` (1 linha, aditivo, sem alterar `run_query`). Assim T4 é auto-testável sem depender de T5; T5 apenas o populpa. (Merge backward do campo dataclass para dentro de T4.)
**Requirement:** MP-10

**Done when:**
- [ ] `GenerationResult.unanswered_sub_questions: list[str] = field(default_factory=list)` adicionado (aditivo, retrocompatível).
- [ ] `QueryResponse.unanswered_sub_questions: list[str] = Field(default_factory=list)` adicionado; `from_result` mapeia.
- [ ] Testes em `tests/api/test_query.py`: `QueryResponse` com default `[]` (retrocompatibilidade — respostas existentes continuam válidas) e com lista preenchida serializa corretamente (MP-10).
- [ ] Gate check passes: `pytest tests/api/test_query.py -v`
- [ ] Test count: testes existentes de `test_query.py` continuam passando + novos de serialização.

**Tests:** integration (api/schemas = "none" na matrix; serialização testada via `TestClient`/`QueryResponse`)
**Gate:** full

**Commit:** `feat: expõe sub-perguntas não respondidas no contrato flat (RAG-03)`

---

### T5: Orquestração síncrona — `_run_single` + hook de decomposição + `_merge_sub_results`

**What:** Refatorar `run_query` em `generation/chain.py`: extrair o corpo atual para `_run_single` (verbatim), adicionar o hook `subs = decompose_query(question, settings)` e `_merge_sub_results` (re-numera citações 1-based, remapeia `[N]` via `remap_answer`, concatena respostas, preenche `unanswered_sub_questions`, cold start total se nenhuma citação válida). `run_query`: `len==1` → `_run_single`; `len>1` → merge.
**Where:** `src/medasist/generation/chain.py`, `tests/generation/test_chain.py`
**Depends on:** T2 (`decompose_query`), T3 (`remap_answer`); T4 já adicionou o campo dataclass.
**Reuses:** `retrieve`, `build_citations`, `validate_citations`, `get_profile_config`, `PromptRegistry`, `ChatOpenAI` (tudo já em `chain.py`); `decompose_query`, `remap_answer`.
**Requirement:** MP-01, MP-02, MP-03, MP-04, MP-05, MP-06, MP-07, MP-08, MP-09, MP-10, MP-11, MP-13

**Done when:**
- [ ] Flag off → `run_query` = `_run_single(question)` byte-identical; suíte `test_chain.py` atual verde (MP-01).
- [ ] `len>1`: cada sub passa por `_run_single` (funil completo, reescrita curta por sub — MP-06); citações re-numeradas 1-based e `[N]` remapeados no merged (MP-03); merged com ≥1 citação válida + disclaimer (MP-04).
- [ ] Todas-miss → cold start total (MP-09); algumas-miss → hits no merged + `unanswered_sub_questions` preenchido (MP-10); sub sem citação válida tratada como miss com órfãos removidos antes da re-numeração (MP-11).
- [ ] Split falha/0/1 sub → identidade (MP-07/08); cap respeitado (MP-05); sub-perguntas usadas apenas como `question` de cada sub (MP-13).
- [ ] `logger.info` composto em `run_query` (nº subs, hits, misses) — aditivo, sem quebrar `test_query_logging`.
- [ ] Testes em `test_chain.py` cobrem os ACs síncronos acima (com `decompose_query` real mockando só o `ChatOpenAI` de split e de geração).
- [ ] Gate check passes: `pytest tests/generation/test_chain.py -v`
- [ ] Test count: testes novos + existentes de `test_chain.py` passam.

**Tests:** unit (chain.py = unit na matrix)
**Gate:** full

**Commit:** `feat: adiciona decomposição e merge multi-parte no run_query (RAG-03)`

---

### T6: Paridade de streaming — `stream_answer` com decomposição

**What:** Estender `stream_answer` em `generation/chain.py` para o caminho decomposto: `len==1` → caminho atual (byte-identical flag off); `len>1` → para cada sub gera deltas (yield), acumula a resposta e valida, e ao final reusa `_merge_sub_results`; retorna `(merged_citations, is_cold_start)`.
**Where:** `src/medasist/generation/chain.py`, `tests/generation/test_chain.py`
**Depends on:** T5 (reusa `_merge_sub_results`/`remap_answer`)
**Reuses:** `_merge_sub_results`, `remap_answer`, `decompose_query`, estrutura existente de `stream_answer`.
**Requirement:** MP-14, MP-12

**Done when:**
- [ ] Flag off → `stream_answer` byte-identical (caminho atual).
- [ ] `len>1`: deltas de cada sub são yieldados na ordem, a concatenação é a resposta merged, e o retorno é `(merged_citations, is_cold_start)` conforme a política parcial (todas-miss → `([], True)`; ≥1 hit → `(citações re-numeradas, False)`).
- [ ] `unanswered_sub_questions` do merged é computado no retorno (disponível para o `QueryResponse`; schema SSE inalterado — decisão Q1/Q2).
- [ ] Testes `TestStreamDecompose` em `test_chain.py`: deltas concatenados = merged, citações re-numeradas, cold start parcial, identidade flag off.
- [ ] Gate check passes: `pytest tests/generation/test_chain.py -v`
- [ ] Test count: testes novos + existentes de streaming passam.

**Tests:** unit (chain.py = unit na matrix)
**Gate:** full

**Commit:** `feat: adiciona paridade de decomposição no stream_answer (RAG-03)`

---

### T7: Avaliação — `_collect_rows` exercita decomposição (AD-011)

**What:** Garantir que `_collect_rows` em `evaluation/metrics.py` percorra o caminho decomposto do `run_query` (já o faz por chamar `run_query` real); adicionar teste de que uma pergunta composta passa pela decomposição no caminho de avaliação.
**Where:** `tests/evaluation/test_metrics.py` (modify; `metrics.py` sem mudança se já delega a `run_query` — verificar)
**Depends on:** T5
**Reuses:** `run_query` (decomposição ativa), scaffolding de `test_rag03_query_rewrite.py`.
**Requirement:** MP-12

**Done when:**
- [ ] Confirmado que `_collect_rows` chama `run_query` real (decomposição ativa) — sem lógica de decomposição duplicada em `metrics.py`.
- [ ] Teste em `test_metrics.py`: pergunta composta com split mockado → `_collect_rows` produz `cold_flags` e `answer` coerentes com a decomposição (invariante AD-011).
- [ ] Gate check passes: `pytest tests/evaluation/test_metrics.py -v`
- [ ] Test count: testes novos + existentes passam.

**Tests:** unit/integration (evaluation — preenchido por AD-011; sem type exigido na matrix, teste adicionado por precedente)
**Gate:** full

**Commit:** `test: valida caminho de decomposição no _collect_rows (RAG-03)`

---

### T8: Aceite end-to-end — `tests/acceptance/test_rag03_multipart.py`

**What:** Criar suíte de aceite cobrindo MP-01..MP-14 via `retrieve()`/`run_query()`/`stream_answer()`/`_collect_rows()`, LLM de split e de geração mockados (nunca rede), ChromaDB real em `tmp_path`, `_LengthSensitiveEmbeddings`/`_DivergentEmbeddings`.
**Where:** `tests/acceptance/test_rag03_multipart.py`
**Depends on:** T5, T6
**Reuses:** scaffolding de `test_rag03_query_rewrite.py` (fixtures `client`, `_settings`, `_patch_rewrite_llm`, `_LengthSensitiveEmbeddings`, `_DivergentEmbeddings`).
**Requirement:** MP-01..MP-14

**Done when:**
- [ ] Cobertura por AC numerado (MP-01..MP-14): flag off identidade, composta → 2+ subs, merge + re-numeração + remap, ≥1 citação + disclaimer, cap 5, reescrita por sub, falha/malformado/0 → identidade, 1 sub → identidade, todas-miss cold start, algumas-miss + `unanswered_sub_questions`, sub sem citação válida → miss, eval `_collect_rows` mesmo caminho, sub-perguntas só para retrieval, streaming paridade.
- [ ] Nenhum arquivo de `src/` modificado; dados sintéticos.
- [ ] Gate check passes: `pytest tests/acceptance/test_rag03_multipart.py -v`
- [ ] Test count: todos os testes novos passam.

**Tests:** acceptance (full)
**Gate:** full

**Commit:** `test: adiciona aceite end-to-end da decomposição multi-parte (RAG-03)`

---

### T9: Gate full final

**What:** Rodar o gate build completo do repositório.
**Where:** repositório inteiro
**Depends on:** T7, T8
**Reuses:** comandos do AGENTS.md/gate checks.

**Done when:**
- [ ] `pytest tests/ -v --cov=src --cov-fail-under=80` passa (cobertura ≥ 80%).
- [ ] `black src/ tests/ scripts/` não modifica nada (formatado).
- [ ] `ruff check src/ tests/ scripts/` sem erros.
- [ ] Suíte existente (query_rewrite, rerank, hybrid, streaming, chain, citations, api, evaluation) continua verde — identidade flag off.

**Tests:** none (gate)
**Gate:** build

**Commit:** (sem commit de código — gate de validação)

---

## Parallel Execution Map

```
Phase 1 (Sequential):
  T1

Phase 2 (Parallel):
  T1 done, then:
    ├── T2 [P]  (retrieval/decompose + tests)
    ├── T3 [P]  (citations.remap + tests)
    └── T4 [P]  (schemas field + tests)

Phase 3 (Sequential):
  T2, T3 done → T5

Phase 4 (Sequential):
  T5 done → T6

Phase 5 (Parallel):
  T5, T6 done, then:
    ├── T7 [P]  (eval test)
    └── T8 [P]  (acceptance)

Phase 6 (Sequential):
  T7, T8 done → T9
```

**Parallelism constraint:** T2, T3, T4 não dependem entre si e seus tipos de teste (unit/unit/integration) são parallel-safe conforme TESTING.md. T7, T8 dependem de T5/T6 mas não entre si; ambos são parallel-safe. Nenhuma tarefa `[P]` compartilha estado mutável (cada uma usa `tmp_path`-unique ChromaDB ou fixtures mockadas).

---

## Validation Tables

### Check 1 — Task Granularity

| Task | Scope | Status |
|------|-------|--------|
| T1 | 1 arquivo de config + env + 1 classe de teste (settings) | ✅ Granular |
| T2 | 1 módulo novo (`decompose.py`) + 1 arquivo de teste | ✅ Granular |
| T3 | 1 função (`remap_answer`) + 1 arquivo de teste | ✅ Granular |
| T4 | 1 campo aditivo (dataclass + schema) + 1 arquivo de teste | ✅ Granular |
| T5 | 1 arquivo (`chain.py`, orquestração síncrona) + 1 arquivo de teste | ✅ Granular (coeso: `_run_single`+merge no mesmo fluxo) |
| T6 | 1 arquivo (`chain.py`, streaming parity) + 1 arquivo de teste | ✅ Granular |
| T7 | 1 arquivo de teste de avaliação | ✅ Granular |
| T8 | 1 arquivo de teste de aceite | ✅ Granular |
| T9 | gate full | ✅ Granular |

### Check 2 — Diagram-Definition Cross-Check

| Task | Depends On (body) | Diagram Shows | Status |
|------|-------------------|---------------|--------|
| T1 | None | T1 inicial | ✅ Match |
| T2 | T1 | T1→T2 | ✅ Match |
| T3 | None | T3 paralelo a T2/T4 (após T1) | ✅ Match |
| T4 | None | T4 paralelo a T2/T3 | ✅ Match |
| T5 | T2, T3 | T2→T5, T3→T5 | ✅ Match |
| T6 | T5 | T5→T6 | ✅ Match |
| T7 | T5 | T5→T7 | ✅ Match |
| T8 | T5, T6 | T5→T8, T6→T8 | ✅ Match |
| T9 | T7, T8 | T7→T9, T8→T9 | ✅ Match |

### Check 3 — Test Co-location Validation

| Task | Code Layer Created/Modified | Matrix Requires | Task Says | Status |
|------|----------------------------|-----------------|-----------|--------|
| T1 | config.py | none (indirect via fixtures) | unit (`TestSettingsDecompose`, precedente) | ✅ OK |
| T2 | retrieval/decompose.py (new) | retrieval = integration (retriever.py); novo módulo segue precedente `query_rewrite` = unit | unit | ✅ OK |
| T3 | generation/citations.py | unit | unit | ✅ OK |
| T4 | api/schemas.py + GenerationResult field | none (indirect via API 422) | integration (`TestClient`/`QueryResponse`) | ✅ OK |
| T5 | generation/chain.py | unit | unit | ✅ OK |
| T6 | generation/chain.py | unit | unit | ✅ OK |
| T7 | evaluation/test_metrics.py | none (evaluation preenchido por AD-011) | unit/integration | ✅ OK |
| T8 | tests/acceptance/ | — (aceite) | acceptance/full | ✅ OK |
| T9 | — | — | gate | ✅ OK |

Todos ✅ — nenhum teste deferido; cada tarefa de código carrega seus testes co-located no mesmo task.
