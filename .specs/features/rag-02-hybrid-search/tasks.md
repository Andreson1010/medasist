# RAG-02 — Hybrid Search (denso + esparso) — Tasks

**Design:** `.specs/features/rag-02-hybrid-search/design.md`
**Status:** Awaiting human approval

---

## Execution Plan

### Phase 1: Foundation (Parallel OK)

Base de configuração e dependência — T1 e T2 independentes entre si, ambos pré-requisitos do módulo esparso.

```
T1 ─┐
T2 ─┴──→ T3
```

### Phase 2: Módulo esparso (Sequential)

Índice BM25 em memória: tokenizador → construção do índice → busca com reconstrução de metadados.

```
T3 → T4 → T5
```

### Phase 3: Fusão no funil (Sequential)

RRF → integração no `retrieve()` → observabilidade aditiva.

```
T5 → T6 → T7 → T8
```

### Phase 4: Aceite e gate (Sequential)

Suíte de aceite end-to-end e gate completo de qualidade.

```
T8 → T9 → T10
```

---

## Task Breakdown

### T1: Adicionar settings de busca híbrida

**What**: Campos `retrieval_hybrid_enabled=False`, `retrieval_hybrid_rrf_k=60 (gt=0)`, `retrieval_hybrid_sparse_top_k=20 (gt=0, default ≥ retrieval_top_k)` e `retrieval_sparse_stopwords` em `Settings`, documentados em `.env.example`.
**Where**: `src/medasist/config.py` (bloco `# Retrieval`), `.env.example`, `tests/config/test_config.py`
**Depends on**: None
**Reuses**: Padrão do bloco RAG-01 (`Field(default=..., gt=0)`), `csv_list`/`tuple` para stopwords
**Requirement**: HYBR-21, HYBR-22, HYBR-23

**Tools**:
- MCP: NONE
- Skill: `build-with-tests`, `git-workflow`

**Done when**:
- [ ] Defaults presentes e documentados no docstring do `Settings`
- [ ] `.env.example` documenta `RETRIEVAL_HYBRID_ENABLED`, `RETRIEVAL_HYBRID_RRF_K`, `RETRIEVAL_HYBRID_SPARSE_TOP_K`, `RETRIEVAL_SPARSE_STOPWORDS`
- [ ] Testes de default, override por env e fail-fast (`rrf_k=0`, `sparse_top_k=-1`) em `tests/config/test_config.py`
- [ ] Gate check passa: `pytest tests/config/test_config.py -v`
- [ ] Test count: baseline + novos (sem deleções silenciosas)

**Tests**: unit
**Gate**: quick
**Commit**: `feat: adiciona configs de busca híbrida (RRF, top-k esparso)`

---

### T2: Declarar dependência BM25 nos requirements [P]

**What**: Adicionar a biblioteca BM25 **`rank_bm25`** (decisão Q1 — vinculante, ver "Decisões Resolvidas" no spec.md) pinada em `requirements.txt` (base) e `requirements-api.txt` (runtime API).
**Where**: `requirements.txt`, `requirements-api.txt`
**Depends on**: None
**Reuses**: Estrutura de seções dos requirements (`# Reranking cross-encoder (RAG-01)` como exemplo)
**Requirement**: HYBR-19

**Tools**:
- MCP: NONE
- Skill: NONE

**Done when**:
- [ ] Biblioteca declarada e pinada em AMBOS arquivos (lacuna RAG-01 não repetida)
- [ ] `pip install -r requirements.txt -r requirements-api.txt` conclui sem erro
- [ ] Import da biblioteca funciona no ambiente

**Tests**: none
**Gate**: build
**Commit**: `chore: declara dependência de BM25 nos requirements`

---

### T3: Criar tokenizador esparso

**What**: `tokenize(text, settings) -> list[str]` com minúsculas, remoção de diacríticos (acentos), tokens dígito-unidade íntegros ("500mg") e `retrieval_sparse_stopwords` separada (mg/ml/g/kg preservados).
**Where**: `src/medasist/retrieval/sparse.py` (novo), `tests/retrieval/test_sparse.py` (novo)
**Depends on**: T1, T2
**Reuses**: Convenção de regex de tokens do `retriever.py` (`_TOKEN_RE`), estilo NumPy de docstrings
**Requirement**: HYBR-14, HYBR-15, HYBR-16

**Tools**:
- MCP: NONE
- Skill: `build-with-tests`

**Done when**:
- [ ] "Dipironá"/"dipirona" casam (acentos normalizados em ambos os sentidos)
- [ ] "500mg" permanece token único e casa com "Amoxicilina 500mg"
- [ ] `mg`, `ml`, `g`, `kg` NÃO são removidos (lista esparsa não usa `retrieval_stopwords`)
- [ ] Query só com stopwords esparsas → zero tokens
- [ ] Gate check passa: `pytest tests/retrieval/test_sparse.py -v`
- [ ] Test count: baseline + novos (sem deleções silenciosas)

**Tests**: unit
**Gate**: quick
**Commit**: `feat: adiciona tokenizador esparso com normalização de acentos`

---

### T4: Criar índice BM25 lazy por DocType

**What**: `get_sparse_index(store, settings) -> SparseIndex` — singleton lazy por coleção (double-checked locking), snapshot do corpus via `store._collection.get(include=["documents","metadatas"])`, hook `reset_sparse_indexes()` para testes, detecção de staleness para refresh pós-ingest.
**Where**: `src/medasist/retrieval/sparse.py` (novo), `tests/retrieval/test_sparse.py` (novo)
**Depends on**: T3
**Reuses**: Padrão de singleton lazy + degradação graciosa de `reranker.py`; probe de `scripts/evaluate_rag.py` para acesso ao corpus
**Requirement**: HYBR-12, HYBR-18, HYBR-20

**Tools**:
- MCP: NONE
- Skill: `build-with-tests`

**Done when**:
- [ ] Índice construído uma única vez por coleção e reutilizado em queries seguintes
- [ ] Construção concorrente (threads) produz um único índice — sem race
- [ ] Chunks ingeridos após a construção tornam-se visíveis na query seguinte (staleness detectada)
- [ ] Coleção vazia → índice vazio, sem erro
- [ ] `reset_sparse_indexes()` limpa o cache (fixtures não vazam estado entre testes)
- [ ] Gate check passa: `pytest tests/retrieval/test_sparse.py -v` e `pytest tests/retrieval/test_retriever.py -v` (flag off intocado)
- [ ] Test count: baseline + novos (sem deleções silenciosas)

**Tests**: integration
**Gate**: full
**Commit**: `feat: adiciona índice BM25 lazy por DocType`

---

### T5: Implementar busca esparsa com reconstrução de metadados

**What**: `SparseIndex.search(query, top_k) -> list[tuple[Document, float]]` — top-k por score BM25 desc, reconstruindo `Document` com `doc_type`, `source_path`, `sha256`, `chunk_index`, `page`, `section`, `char_count`; falhas logam e degradam para dense-only (nunca propagam).
**Where**: `src/medasist/retrieval/sparse.py` (novo), `tests/retrieval/test_sparse.py` (novo)
**Depends on**: T4
**Reuses**: Shape de metadata do `pipeline.py` (L186–197); `build_citations` como consumidor de verificação
**Requirement**: HYBR-13, HYBR-17

**Tools**:
- MCP: NONE
- Skill: `build-with-tests`

**Done when**:
- [ ] Document reconstruído tem `page_content` e metadados idênticos ao armazenado (paridade com denso)
- [ ] `build_citations` produz CitationItem com `source`/`section`/`page` corretos para chunk esparso (página 0 → vazio, como no denso)
- [ ] `_lexical_relevance_guard` funciona sobre chunk esparso (lê `source`/`source_path`)
- [ ] Falha de consulta → log + lista vazia do esparso, sem exceção propagada
- [ ] Gate check passa: `pytest tests/retrieval/test_sparse.py -v` e `pytest tests/generation/test_citations.py -v`
- [ ] Test count: baseline + novos (sem deleções silenciosas)

**Tests**: integration
**Gate**: full
**Commit**: `feat: adiciona busca esparsa com reconstrução de metadados`

---

### T6: Implementar fusão RRF

**What**: `_rrf_fuse(dense, sparse, k) -> list[tuple[Document, float]]` — score = Σ 1/(k+rank) por lista, dedup por `page_content` somando contribuições do mesmo chunk, ordenação desc, empate determinístico (denso precede esparso).
**Where**: `src/medasist/retrieval/retriever.py` (novo helper privado), `tests/retrieval/test_sparse.py` (novo)
**Depends on**: T5
**Reuses**: Dedup por `page_content` (padrão atual de `retrieve()`), ordenação estável (padrão reranker)
**Requirement**: HYBR-03, HYBR-08, HYBR-11

**Tools**:
- MCP: NONE
- Skill: `build-with-tests`

**Done when**:
- [ ] Chunk no rank 1 denso + rank 3 esparso com k=60 pontua exatamente 1/61 + 1/63
- [ ] Mesmo `page_content` nos dois caminhos aparece uma única vez com score somado
- [ ] Empate de score RRF → ordem estável determinística
- [ ] Listas vazias de entrada → resultado vazio sem erro
- [ ] Gate check passa: `pytest tests/retrieval/test_sparse.py -v`
- [ ] Test count: baseline + novos (sem deleções silenciosas)

**Tests**: unit
**Gate**: quick
**Commit**: `feat: adiciona fusão RRF com dedup determinístico`

---

### T7: Integrar branch híbrido no funil `retrieve()`

**What**: Branch flag-gated em `retrieve()`: dense (inalterado) + esparso (top-k) → `_rrf_fuse` → guarda lexical → rerank (se habilitado) → `[:k]`. Cold start decidido pós-fusão e pós-guarda (hit esparso aprovado ≠ cold start). Flag off → identidade total (sem tocar no índice esparso).
**Where**: `src/medasist/retrieval/retriever.py` (modify), `tests/retrieval/test_retriever.py` (modify)
**Depends on**: T6
**Reuses**: `select_collections`, `_lexical_relevance_guard`, `rerank_documents`, early-return reworkado (L177)
**Requirement**: HYBR-01, HYBR-02, HYBR-04, HYBR-05, HYBR-06, HYBR-07, HYBR-09, HYBR-10

**Tools**:
- MCP: NONE
- Skill: `build-with-tests`

**Done when**:
- [ ] Flag off (default): `retrieve()` idêntico ao atual; índice esparso não construído nem importado; mocks `MagicMock(spec=Chroma)` sem `_collection` passam
- [ ] Flag on: ordem final = denso → esparso → dedup → RRF → guarda → rerank → `[:top_k]`
- [ ] Flag on com denso vazio + esparso com hit exato aprovado pela guarda → retorna chunk (não cold start)
- [ ] Flag on com ambos vazios → `[]` (cold start; `run_query` devolve `cold_start_message` sem LLM)
- [ ] Guarda lexical bloqueia chunk esparso de outro fármaco (query dipirona × chunk ibuprofeno → `[]`)
- [ ] `doc_types=[BULA]` limita candidatos esparsos à coleção de bulas
- [ ] Contrato `list[Document]` e assinaturas `retrieve`/`run_query`/`build_retriever`/`select_collections` inalterados
- [ ] Gate check passa: `pytest tests/retrieval/test_retriever.py -v` e `pytest tests/ -v --cov=src --cov-fail-under=80`
- [ ] Test count: baseline + novos (sem deleções silenciosas)

**Tests**: integration
**Gate**: full
**Commit**: `feat: integra busca híbrida no funil retrieve`

---

### T8: Estender observabilidade do retrieval

**What**: Campos aditivos no record consolidado de `_log_retrieve_metric` (ex.: `hybrid=True`, contagens de candidatos denso/esparso) mantendo `chunks`, `scores`, `latency_ms`, `cold_start`, `doc_types`, `failed_stores`.
**Where**: `src/medasist/retrieval/retriever.py` (modify), `tests/retrieval/test_retriever.py` (modify), `tests/acceptance/test_obs_01_logging.py` (modify)
**Depends on**: T7
**Reuses**: `_log_retrieve_metric` existente (formato `%s`-placeholders, query truncada em 50 chars)
**Requirement**: HYBR-24

**Tools**:
- MCP: NONE
- Skill: `build-with-tests`

**Done when**:
- [ ] Record com híbrido contém contagens distintas de denso e esparso
- [ ] Record sem híbrido permanece exatamente como hoje (testes `test_obs_01_logging.py` e `test_retriever.py` verdes)
- [ ] Nenhum dado de paciente logado (padrão existente)
- [ ] Gate check passa: `pytest tests/retrieval/test_retriever.py -v tests/acceptance/test_obs_01_logging.py -v`
- [ ] Test count: baseline + novos (sem deleções silenciosas)

**Tests**: unit
**Gate**: quick
**Commit**: `feat: expõe métricas denso/esparso no log de retrieval`

---

### T9: Escrever suíte de aceite do híbrido

**What**: `tests/acceptance/test_rag02_hybrid_search.py` espelhando o padrão de `test_rag01_reranking.py` — ACs numeradas: identidade flag off, RRF math, hit esparso gera resposta com citação, cold start pós-fusão, guarda sobre esparso, invariante eval (`_collect_rows` usa o caminho híbrido).
**Where**: `tests/acceptance/test_rag02_hybrid_search.py` (novo)
**Depends on**: T7, T8
**Reuses**: Padrão de ACs e helpers de `tests/acceptance/test_rag01_reranking.py`; `_FakeEmbeddings`/`_DivergentEmbeddings` de `test_retriever.py`; fixture `settings` do `tests/conftest.py`
**Requirement**: HYBR-01, HYBR-03, HYBR-05, HYBR-09, HYBR-17

**Tools**:
- MCP: NONE
- Skill: `build-with-tests`

**Done when**:
- [ ] Cada AC do spec mapeada para teste binário (pass/fail) no arquivo
- [ ] AC de hit esparso valida `build_citations` com `source`/`section`/`page` corretos
- [ ] AC de eval chama `_collect_rows` (ou `retrieve`) e comprova caminho idêntico à API
- [ ] Gate check passa: `pytest tests/acceptance/test_rag02_hybrid_search.py -v`
- [ ] Test count: baseline + novos (sem deleções silenciosas)

**Tests**: integration
**Gate**: full
**Commit**: `test: adiciona testes de aceite do híbrido`

---

### T10: Gate completo e atualização de status

**What**: Rodar formatação, lint e suíte completa com gate de cobertura; atualizar status no `tasks.md`/traceability; revisão de código com skill `code-reviewer`.
**Where**: Repositório inteiro; `.specs/features/rag-02-hybrid-search/`
**Depends on**: T9
**Reuses**: Gate commands de `.specs/codebase/TESTING.md`
**Requirement**: Todos

**Tools**:
- MCP: NONE
- Skill: `code-reviewer`, `git-workflow`

**Done when**:
- [ ] `black src/ tests/ scripts/ && ruff check src/ tests/ scripts/` limpos
- [ ] `pytest tests/ -v --cov=src --cov-fail-under=80` passa (≥ 80% cobertura; baseline 452+ testes de AD-013 sem deleções)
- [ ] Traceability no `spec.md` atualizado (24 IDs mapeados → tasks)
- [ ] Code review executado e pendências resolvidas

**Tests**: none (gate global)
**Gate**: build
**Commit**: `chore: aplica gate completo do híbrido`

---

## Parallel Execution Map

```
Phase 1 (Parallel OK):
  T1 ─┐
  T2 ─┴──→ T3

Phase 2 (Sequential):
  T3 → T4 → T5

Phase 3 (Sequential):
  T5 → T6 → T7 → T8

Phase 4 (Sequential):
  T8 → T9 → T10
```

**Parallelism constraint:** apenas T2 é `[P]` (sem testes, sem dependência de código — declaração de requirements). T1 e T2 rodam em sub-agentes paralelos; os demais são sequenciais por acoplamento no funil `retrieve()` e por compartilharem o módulo `sparse.py`/`retriever.py`.

---

## Task Granularity Check

| Task | Scope | Status |
|------|-------|--------|
| T1: settings híbridas | 1 arquivo de config + env example + testes de config | ✅ Granular |
| T2: dependência BM25 | 2 arquivos de requirements | ✅ Granular (1 conceito) |
| T3: tokenizador esparso | 1 função + testes | ✅ Granular |
| T4: índice BM25 lazy | 1 componente + testes | ✅ Granular |
| T5: busca esparsa + metadados | 1 componente + testes | ✅ Granular |
| T6: fusão RRF | 1 helper + testes | ✅ Granular |
| T7: branch híbrido no retrieve | 1 funil + testes | ✅ Granular (1 função-chave) |
| T8: observabilidade aditiva | 1 helper de log + testes | ✅ Granular |
| T9: suíte de aceite | 1 arquivo de teste | ✅ Granular |
| T10: gate completo | gate + status | ✅ Granular |

## Diagram-Definition Cross-Check

| Task | Depends On (task body) | Diagram Shows | Status |
|------|------------------------|---------------|--------|
| T1 | None | T1 início da Phase 1 | ✅ Match |
| T2 | None | T2 início da Phase 1 | ✅ Match |
| T3 | T1, T2 | T1/T2 → T3 | ✅ Match |
| T4 | T3 | T3 → T4 | ✅ Match |
| T5 | T4 | T4 → T5 | ✅ Match |
| T6 | T5 | T5 → T6 | ✅ Match |
| T7 | T6 | T6 → T7 | ✅ Match |
| T8 | T7 | T7 → T8 | ✅ Match |
| T9 | T7, T8 | T8 → T9 (T7 ⊂ dependência implícita de T8) | ✅ Match |
| T10 | T9 | T9 → T10 | ✅ Match |

## Test Co-location Validation

| Task | Code Layer Created/Modified | Matrix Requires | Task Says | Status |
|------|-----------------------------|-----------------|-----------|--------|
| T1 | `config.py` (+ `.env.example`) | none (indirect) — precedente RAG-01 adicionou testes | unit (`tests/config/test_config.py`) | ✅ OK (upgrade do mínimo, alinhado a AD-013) |
| T2 | `requirements*.txt` | none | none | ✅ OK |
| T3 | `retrieval/sparse.py` (tokenizer) | new module — padrão retriever = integration; tokenizer = unit | unit (`tests/retrieval/test_sparse.py`) | ✅ OK |
| T4 | `retrieval/sparse.py` (index) | new module — integration | integration (`tests/retrieval/test_sparse.py`) | ✅ OK |
| T5 | `retrieval/sparse.py` (search) | new module — integration | integration (`tests/retrieval/test_sparse.py`) | ✅ OK |
| T6 | `retrieval/retriever.py` (helper) | integration | unit (`tests/retrieval/test_sparse.py`) | ✅ OK (helper puro, sem I/O) |
| T7 | `retrieval/retriever.py` | integration | integration (`tests/retrieval/test_retriever.py`) | ✅ OK |
| T8 | `retrieval/retriever.py` (log) | integration | unit (log assertions em `test_retriever.py`/`test_obs_01_logging.py`) | ✅ OK |
| T9 | `tests/acceptance/` | acceptance (padrão RAG-01) | integration (aceite) | ✅ OK |
| T10 | gate global | — | none | ✅ OK |

Nenhuma violação. Tarefas que criam código incluem seus testes no mesmo task (sem test deferral).