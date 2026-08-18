# RAG-01: Reranking Cross-Encoder — Tasks

**Design:** `.specs/features/rag-01-reranking/design.md`
**Spec:** `.specs/features/rag-01-reranking/spec.md`
**Status:** Awaiting human approval

---

## Execution Plan

### Phase 1: Foundation (Parallel OK)

Dependência nova (T1) e configuração (T2) são independentes; T3 precisa de ambas.

```
T1 ──┐
     ├──→ T3
T2 ──┘
```

### Phase 2: Core Implementation (Parallel OK)

Depois de T3, o plug no retriever e o MRR são independentes (T5 depende apenas de T2).

```
T2 ──→ T5 ─┐
T3 ──→ T4 ─┼──→ T6
           └──┘
```

### Phase 3: Integration (Sequential)

Fechamento: contrato da chain e relatório do eval.

```
T3, T4, T5 done:
T6 → T7
```

---

## Task Breakdown

### T1: [Adicionar dependência sentence-transformers]

**What**: Adicionar `sentence-transformers` (versão compatível com Python 3.11) a `requirements.txt` (runtime) e, se necessário, `requirements-dev.txt`.
**Where**: `requirements.txt`, `requirements-dev.txt`
**Depends on**: None
**Reuses**: convenção de pinning existente (ex.: `ragas==0.2.15`)
**Requirement**: RAG01-05, RAG01-12

**Tools**:
- MCP: NONE
- Skill: NONE

**Done when**:
- [ ] `sentence-transformers` listado com versão pinada em `requirements.txt`
- [ ] `pip install -r requirements.txt` resolve sem erro
- [ ] Gate check passa: `python -c "import sentence_transformers"`

**Tests**: none (dependência)
**Gate**: quick

---

### T2: [Adicionar settings retrieval_rerank_* + .env.example + testes] [P]

**What**: Adicionar as 4 settings (`retrieval_rerank_enabled`, `retrieval_rerank_model`, `retrieval_rerank_top_n`, `retrieval_rerank_batch_size`) ao bloco `# Retrieval` de `config.py`, documentar em `.env.example` e escrever testes de validação.
**Where**: `src/medasist/config.py`, `.env.example`, `tests/config/test_config.py` (novo)
**Depends on**: None (config.py não importa `sentence_transformers` — pode rodar em paralelo a T1)
**Reuses**: padrão `Field(default=..., gt=0)` existente
**Requirement**: RAG01-11, RAG01-14

**Tools**:
- MCP: NONE
- Skill: NONE

**Done when**:
- [ ] Defaults: `enabled=False`, `model="BAAI/bge-reranker-base"`, `top_n=20`, `batch_size=16`
- [ ] `retrieval_rerank_top_n` e `retrieval_rerank_batch_size` têm `gt=0` (rejeitam 0/negativo via pydantic)
- [ ] `.env.example` documenta as 4 variáveis (ex.: `RETRIEVAL_RERANK_ENABLED=false`, `RETRIEVAL_RERANK_MODEL=BAAI/bge-reranker-base`, `RETRIEVAL_RERANK_TOP_N=20`, `RETRIEVAL_RERANK_BATCH_SIZE=16`)
- [ ] Gate check passa: `pytest tests/config/test_config.py -v` (testes de default, override via env, e `gt=0`)
- [ ] Test count: N novos testes passam (sem deleção silenciosa)

**Tests**: unit
**Gate**: quick

> Nota (test co-location): `TESTING.md` marca `config.py` como "none (indirect via fixtures)". As novas settings introduzem constraints validadas (`gt=0`) que exigem verificação explícita; por isso este task inclui `tests/config/test_config.py` como melhoria da lacuna conhecida L1.

---

### T3: [Criar reranker.py — helper de reordenação + singleton lazy] [P]

**What**: Criar `src/medasist/retrieval/reranker.py` com `rerank_documents(docs, query, settings)` que: (a) obtém o `CrossEncoder` via singleton lazy thread-safe (`_get_reranker` com `threading.Lock` + double-checked locking), (b) monta até `rerank_top_n` pares `(query, doc.page_content)` e chama `predict` em batch único, (c) reordena por score desc com tie-break determinístico pela ordem L2 original, (d) em falha, loga `logger.exception` e retorna `docs` inalterados (ordem L2). Escrever `tests/retrieval/test_reranker.py`.
**Where**: `src/medasist/retrieval/reranker.py` (novo), `tests/retrieval/test_reranker.py` (novo)
**Depends on**: T1 (dependência `sentence-transformers` instalada), T2 (settings)
**Reuses**: padrão singleton de `store.get_client`; convenções de logging e docstrings NumPy PT-BR
**Requirement**: RAG01-01, RAG01-02, RAG01-05, RAG01-07, RAG01-12, RAG01-13

**Tools**:
- MCP: NONE
- Skill: NONE

**Done when**:
- [ ] `rerank_documents` reordena por score desc (mock do `CrossEncoder` retornando scores)
- [ ] Singleton: `_get_reranker` carregado uma única vez (assert de chamada única ao construtor) — RAG01-12
- [ ] Batch único sobre até `rerank_top_n` pares — RAG01-02
- [ ] Tie-break determinístico: empates preservam a ordem L2 original — edge case
- [ ] Falha do `predict` → retorna `docs` na ordem L2, `logger.exception` chamado, sem exceção propagada — RAG01-05
- [ ] Desabilitado (chamado com flag off) → retorna `docs` inalterados e não instancia o modelo — RAG01-07
- [ ] Gate check passa: `pytest tests/retrieval/test_reranker.py -v`
- [ ] Test count: N testes passam (sem deleção silenciosa)

**Tests**: unit
**Gate**: quick

---

### T4: [Integrar rerank em retrieve() + atualizar test_retriever.py] [P]

**What**: Plug o estágio de rerank em `retrieve()` (`src/medasist/retrieval/retriever.py`) entre `_lexical_relevance_guard` e o corte `guarded[:k]`: se `retrieval_rerank_enabled` e `guarded` não-vazio, chama `rerank_documents` e então `[:k]`; senão mantém `guarded[:k]` (ordem L2). Atualizar `tests/retrieval/test_retriever.py`.
**Where**: `src/medasist/retrieval/retriever.py` (modify), `tests/retrieval/test_retriever.py` (modify)
**Depends on**: T2, T3
**Reuses**: `_lexical_relevance_guard`, `_log_retrieve_metric`
**Requirement**: RAG01-01, RAG01-06, RAG01-08, RAG01-10, RAG01-13

**Tools**:
- MCP: NONE
- Skill: NONE

**Done when**:
- [ ] Flag off → `retrieve()` retorna ordem L2 (identidade), sem instanciar o modelo — RAG01-07
- [ ] Flag on + `CrossEncoder` mockado → docs reordenados por score do reranker, máx. `retrieval_top_k` — RAG01-01, RAG01-10
- [ ] Cold start (nenhum candidato L2) → retorna `[]`, reranker **não** chamado (mock `assert_not_called`) — RAG01-06
- [ ] Cold start lexical (guarda esvazia) → retorna `[]`, reranker não chamado — RAG01-13
- [ ] Rerank não transforma não-cold-start em cold start (flag on, contexto válido permanece não-vazio) — RAG01-08
- [ ] Contrato: retorno continua `list[Document]` — RAG01-09
- [ ] Gate check passa: `pytest tests/retrieval/test_retriever.py -v`
- [ ] Test count: N testes passam (existentes + novos; sem deleção silenciosa)

**Tests**: integration
**Gate**: full

---

### T5: [Adicionar MRR customizado ao eval RAGAS + testes] [P]

**What**: Em `src/medasist/evaluation/metrics.py`, adicionar `_reciprocal_rank(contexts, reference_contexts)`, `_aggregate_mrr(rows, eval_indices)` e expor `mrr` em `QuestionEvalRow.metrics` e `EvaluationReport.aggregates`, agregado sobre o subconjunto não-cold-start. Atualizar `tests/evaluation/test_metrics.py`.
**Where**: `src/medasist/evaluation/metrics.py` (modify), `tests/evaluation/test_metrics.py` (modify)
**Depends on**: T2
**Reuses**: `_collect_rows` (já usa `retrieve()`), `QuestionEvalRow`, `EvaluationReport`
**Requirement**: RAG01-15

**Tools**:
- MCP: NONE
- Skill: NONE

**Done when**:
- [ ] `_reciprocal_rank` retorna 1/rank do primeiro hit de `reference_contexts` em `contexts`; 0 quando nenhum
- [ ] `_aggregate_mrr` calcula a média sobre o subconjunto não-cold-start
- [ ] `aggregates["mrr"]` presente no `EvaluationReport` (e `metrics["mrr"]` por pergunta quando não-cold-start)
- [ ] Gate check passa: `pytest tests/evaluation/test_metrics.py -v`
- [ ] Test count: N testes passam (sem deleção silenciosa)

**Tests**: unit
**Gate**: quick

---

### T6: [Verificar contrato da chain + citações com contexto rerankado]

**What**: Adicionar/ajustar teste em `tests/generation/test_chain.py` provando que `run_query` não muda de contrato com `retrieval_rerank_enabled=True` e que as citações `[N]` geradas sobre contexto rerankado continuam mapeando para `CitationItem` válidos.
**Where**: `tests/generation/test_chain.py` (modify)
**Depends on**: T4 (retrieve com rerank), T3
**Reuses**: fixtures existentes de `test_chain.py`
**Requirement**: RAG01-04, RAG01-09

**Tools**:
- MCP: NONE
- Skill: NONE

**Done when**:
- [ ] `run_query` com flag on e contexto rerankado retorna `GenerationResult` com citações válidas
- [ ] Sem citações válidas → fallback cold start (regra de segurança) inalterado
- [ ] Contrato de `run_query`/`build_chain` intacto
- [ ] Gate check passa: `pytest tests/generation/test_chain.py -v`
- [ ] Test count: N testes passam (sem deleção silenciosa)

**Tests**: unit
**Gate**: quick

---

### T7: [Expor MRR no relatório do evaluate_rag + teste de script]

**What**: Garantir que `scripts/evaluate_rag.py` exiba `mrr` (e demais agregadas) no relatório e atualizar `tests/scripts/test_evaluate_rag.py` para cobrir a presença da chave `mrr` quando `EvaluationReport` a contém.
**Where**: `scripts/evaluate_rag.py` (modify), `tests/scripts/test_evaluate_rag.py` (modify)
**Depends on**: T5
**Reuses**: formatação de relatório existente
**Requirement**: RAG01-15

**Tools**:
- MCP: NONE
- Skill: NONE

**Done when**:
- [ ] Relatório do script inclui `mrr` nas agregadas de retrieval (não-cold-start)
- [ ] Gate check passa: `pytest tests/scripts/test_evaluate_rag.py -v`
- [ ] Test count: N testes passam (sem deleção silenciosa)

**Tests**: unit
**Gate**: quick

---

## Parallel Execution Map

```
Phase 1 (Parallel):
  T1 ──┐
       ├──→ T3
  T2 ──┘

Phase 2 (Parallel):
  T2 complete → T5 starts; T3 complete → T4 starts:
    T4 [P] ─┐
    T5 [P] ─┴──→ T6

Phase 3 (Sequential):
  T6 ──→ T7
```

**Parallelism constraint:** T1 e T2 são independentes (T2 não importa a lib). T3 depende de T1+T2. T4 depende de T3; T5 depende de T2 — T4 e T5 podem rodar em paralelo e têm testes paralelo-safe (TESTING.md). T6 depende de T4 (e de T3); T7 depende de T5.

---

## Pre-Approval Validation

### Check 1: Task Granularity

| Task | Scope | Status |
|------|-------|--------|
| T1 | 1 dependência (requirements) | ✅ Granular |
| T2 | 1 bloco de config + env + teste | ✅ Granular |
| T3 | 1 módulo novo + seu teste | ✅ Granular |
| T4 | 1 função modificada + teste | ✅ Granular |
| T5 | 1 módulo modificado (MRR) + teste | ✅ Granular |
| T6 | 1 arquivo de teste | ✅ Granular |
| T7 | 1 script + teste | ✅ Granular |

### Check 2: Diagram-Definition Cross-Check

| Task | Depends On (body) | Diagram Shows | Status |
|------|-------------------|---------------|--------|
| T1 | None | — | ✅ Match |
| T2 | None | — | ✅ Match |
| T3 | T1, T2 | T1 → T3, T2 → T3 | ✅ Match |
| T4 | T2, T3 | T3 → T4 | ✅ Match |
| T5 | T2 | T2 → T5 | ✅ Match |
| T6 | T4, T3 | T4,T3 → T6 | ✅ Match |
| T7 | T5 | T5 → T7 | ✅ Match |

### Check 3: Test Co-location Validation

| Task | Code Layer Created/Modified | Matrix Requires | Task Says | Status |
|------|----------------------------|-----------------|-----------|--------|
| T1 | requirements (dep) | none | none | ✅ OK |
| T2 | `config.py` (settings) | none (indirect) | unit (novo test) | ✅ OK (melhoria L1) |
| T3 | `reranker.py` (novo) | n/a (novo, unit) | unit | ✅ OK |
| T4 | `retriever.py` | integration | integration | ✅ OK |
| T5 | `metrics.py` (MRR) | n/a (eval stub) | unit | ✅ OK |
| T6 | `chain.py` (test-only) | unit | unit | ✅ OK |
| T7 | `scripts/evaluate_rag.py` | unit | unit | ✅ OK |

---

## Task Verification Standards (gate commands)

| Gate | Command |
|------|---------|
| Quick | `pytest tests/<module> -v` |
| Full | `pytest tests/ -v --cov=src --cov-fail-under=80` |
| Build (fim do feature) | `black src/ tests/ scripts/ && ruff check src/ tests/ scripts/ && pytest tests/ -v --cov=src --cov-fail-under=80` |

Commit format (português, imperativo, conforme AGENTS.md): `feat(retrieval): adiciona reranking cross-encoder no retrieve`, `feat(evaluation): adiciona métrica MRR no eval RAGAS`, etc. Antes de abrir PR: rodar skill `code-reviewer` (AGENTS.md).
