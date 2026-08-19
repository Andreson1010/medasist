# RAG-02 — Hybrid Search (denso + esparso) — Technical Spec

**Path:** `.specs/features/rag-02-hybrid-search/spec.md`
**TLC scope:** large
**Based on story:** "combinar retrieval denso (similarity_search/embeddings) com retrieval esparso (BM25/keyword), fundidos via Reciprocal Rank Fusion (RRF). Crítico para nomes de medicamentos e dosagens exatas."
**Status:** Awaiting human approval

---

## Problem Statement

O retrieval atual é exclusivamente denso (ANN por embeddings, filtro L2 com threshold). Para nomes exatos de medicamentos e dosagens ("dipirona 500mg", "amoxicilina 10 mg/kg/dia"), a similaridade semântica aproximada pode falhar ou recuperar chunks de fármacos vizinhos, gerando cold start ou contexto errado. RAG-02 adiciona um caminho esparso (BM25/keyword) dentro do funil único `retrieve()`, fundido ao denso por RRF, de modo que matches lexicais exatos passem a ser recuperáveis — sem alterar contrato externo e sem quebrar o comportamento atual enquanto a feature estiver desabilitada (flag off por padrão).

## Goals

- [ ] Permitir que perguntas com nomes exatos de medicamentos/dosagens recuperem o chunk correto mesmo quando o caminho denso não o encontra (hit esparso válido, não cold start).
- [ ] Preservar identidade total do comportamento atual quando `retrieval_hybrid_enabled=False` (default), mantendo a suíte existente verde.
- [ ] Manter o contrato externo intacto: `retrieve()` continua retornando `list[Document]`, `POST /query` inalterado, caminho de avaliação (AD-011) idêntico.
- [ ] Adicionar configuração e observabilidade para ativar/calibrar o híbrido com segurança.

## Out of Scope

| Feature | Reason |
|---------|--------|
| Persistir índice BM25 em disco / sidecar index | Índice é em memória, reconstruído do ChromaDB (decisão AD-011 e simplicidade operacional) |
| Elasticsearch/OpenSearch ou serviço externo de busca | Infraestrutura nova fora do escopo do roadmap M4 |
| Query transformation (reescrita, expansão, sinônimos) | Feature RAG-03 separada no roadmap |
| Mudanças no contrato `POST /query` (QueryRequest/QueryResponse) ou prompts | Contrato da API é estável; prompts são do escopo de profiles |
| Alterar caminho denso existente (L2, threshold, dedup, stopwords densas, guarda lexical) | Preservar comportamento atual quando flag off |
| Alterar/duplicar o reranking RAG-01 | Rerank apenas se posiciona após a fusão |
| Pesos por aprendizado de ranking / learning to rank | Complexidade sem evidência de ganho no corpus atual |
| Cache de respostas ou de retrieval | Fora do escopo |
| Suporte multilíngue na tokenização esparsa | Tokenização otimizada para português |

---

## User Stories

### P1: Fusão híbrida (denso + esparso) no funil único `retrieve()` ⭐ MVP

**User Story**: Como profissional de saúde que consulta nomes exatos de medicamentos e dosagens, quero que o retrieval combine busca densa (embeddings) e busca esparsa (BM25) fundidas por RRF dentro do funil único `retrieve()`, para que perguntas como "dose de dipirona 500mg" encontrem o chunk correto mesmo quando a similaridade semântica sozinha falha.
**Why P1**: É o caso de uso crítico do roadmap ("nomes de medicamentos e dosagens exatas"); sem ele, o híbrido não tem valor médico.

**Acceptance Criteria**:

1. WHEN `retrieval_hybrid_enabled=False` (default) e `retrieve()` é chamada THEN system SHALL retornar resultado idêntico ao atual (mesmos documentos, mesma ordem L2, mesmas decisões de cold start) e NÃO construir índice esparso nem importar a biblioteca esparsa. *(HYBR-01)*
2. WHEN híbrido ativo THEN system SHALL manter o contrato `list[Document]` em `retrieve()`/`run_query`/`build_retriever`/`select_collections` e o schema `POST /query` (QueryRequest/QueryResponse) inalterado. *(HYBR-02)*
3. WHEN um chunk está no rank 1 denso e rank 3 esparso com `retrieval_hybrid_rrf_k=60` THEN system SHALL computar score RRF = 1/61 + 1/63 e ordenar a lista final por score RRF decrescente. *(HYBR-03)*
4. WHEN híbrido ativo THEN system SHALL aplicar a ordem: denso filtrado por L2 → esparso top-k → dedup → fusão RRF → guarda lexical → rerank (se habilitado) → corte em `retrieval_top_k`. *(HYBR-04)*
5. WHEN denso vazio (nenhum chunk com L2 ≤ threshold) e esparso recupera chunk com o nome exato do medicamento aprovado pela guarda lexical THEN system SHALL retornar o chunk (hit esparso NÃO é cold start) e `run_query` SHALL gerar resposta com citação. *(HYBR-05)*
6. WHEN denso vazio E esparso vazio (ou todos eliminados pela guarda) THEN system SHALL retornar `[]` e `run_query` SHALL devolver `cold_start_message` sem chamar o LLM. *(HYBR-06)*
7. WHEN query menciona fármaco (ex.: "dipirona") e esparso recupera apenas chunks de outro fármaco (ex.: ibuprofeno) THEN system SHALL esvaziar o contexto via guarda lexical → `[]` (cold start), sem contornar a guarda. *(HYBR-07)*
8. WHEN o mesmo `page_content` vem do denso (rank d) e do esparso (rank s) THEN system SHALL retornar o chunk uma única vez com score RRF = 1/(k+d) + 1/(k+s). *(HYBR-08)*
9. WHEN a avaliação RAG executa `_collect_rows` com híbrido habilitado THEN system SHALL usar exatamente o mesmo caminho híbrido da API — nenhuma lógica de híbrido fora de `retrieve()`. *(HYBR-09)*
10. WHEN `doc_types=[BULA]` é selecionado por `select_collections` THEN system SHALL limitar candidatos esparsos à coleção de bulas (mesmo isolamento per-DocType do denso). *(HYBR-10)*
11. WHEN dois chunks têm score RRF idêntico THEN system SHALL aplicar ordem determinística (ordenação estável; candidato denso precede esparso em empate). *(HYBR-11)*

**Independent Test**: `pytest tests/acceptance/test_rag02_hybrid_search.py` — com `retrieval_hybrid_enabled=True`, query "dose de dipirona" sobre corpus com bula de dipirona mas embeddings divergentes (denso vazio) retorna o chunk da bula; com flag off, `tests/retrieval/test_retriever.py` passa integralmente.

---

### P1: Índice BM25 esparso por DocType com tokenização normalizada

**User Story**: Como mantenedor do MedAssist, quero um índice BM25 em memória, por DocType, construído lazy a partir da coleção ChromaDB e com tokenização normalizada (sem acentos, preservando dosagens), para que a busca esparsa por nomes exatos funcione sem infraestrutura externa e sem degradar o caminho denso.
**Why P1**: A qualidade do match esparso depende do índice e da tokenização; é pré-requisito da fusão (Story 1).

**Acceptance Criteria**:

1. WHEN híbrido ativo e primeira query THEN system SHALL construir o índice BM25 uma única vez por DocType a partir do conteúdo e metadados da coleção ChromaDB (mesmo acesso do probe de avaliação, ex.: `store._collection.get`) e reutilizá-lo nas queries seguintes, com construção protegida para concorrência (double-checked locking). *(HYBR-12)*
2. WHEN falha na construção ou consulta do índice esparso THEN system SHALL logar a falha e seguir com dense-only — nunca propagar exceção nem esvaziar contexto denso válido. *(HYBR-13)*
3. WHEN corpus sem acentos ("bula de dipirona") e query com acentos ("Dipironá") — ou inverso THEN system SHALL normalizar (minúsculas + remoção de diacríticos) e casar os termos de forma equivalente. *(HYBR-14)*
4. WHEN query "amoxicilina 500mg" THEN system SHALL manter "500mg" como token íntegro e casá-lo com "Amoxicilina 500mg" do corpus; WHEN dosagens "10 mg" (com espaço) e "10mg" (sem espaço) aparecem THEN system SHALL normalizar removendo o espaço dígito-unidade e casá-las como o mesmo token `10mg` (decisão Q6 — vinculante). *(HYBR-15)*
5. WHEN a tokenização esparsa roda THEN system SHALL usar `retrieval_sparse_stopwords` própria e NÃO aplicar `retrieval_stopwords` (que contém `mg`, `ml`, `g`, `kg` — preservados no esparso). *(HYBR-16)*
6. WHEN chunk recuperado apenas pelo esparso THEN system SHALL reconstruir o `Document` com `doc_type`, `source_path`, `sha256`, `chunk_index`, `page` e `section` idênticos ao armazenado — e `build_citations`/`_lexical_relevance_guard` SHALL produzir o mesmo resultado do caminho denso. *(HYBR-17)*
7. WHEN novo chunk é ingerido via `/ingest` na mesma coleção após o índice construído THEN system SHALL torná-lo recuperável pelo esparso em query seguinte (índice refletido/invalidado). *(HYBR-18)*
8. WHEN o ambiente da API instala `requirements-api.txt` THEN system SHALL declarar a biblioteca BM25 (pura-Python) e permitir sua importação no runtime da API — sem repetir a lacuna do RAG-01 (sentence-transformers ausente de requirements-api.txt). *(HYBR-19)*
9. WHEN coleção está vazia THEN system SHALL retornar zero candidatos esparsos sem erro; combinado com denso vazio, SHALL resultar em cold start `[]`. *(HYBR-20)*

**Independent Test**: `pytest tests/retrieval/test_sparse.py` — índice construído sobre coleção ChromaDB real com `_FakeEmbeddings`, busca por "Dipironá" casa chunk "bula de dipirona"; dosagem "500mg" casa "Amoxicilina 500mg"; `build_citations` retorna section/page corretos.

---

### P2: Configuração e observabilidade do híbrido

**User Story**: Como mantenedor, quero controlar e auditar o híbrido via configuração (flag off por padrão, constante RRF, top-k esparso) e métricas de log que distinguem a contribuição de cada caminho, para ativar a feature com segurança e calibrá-la com dados.
**Why P2**: Necessário para rollout gradual e diagnóstico, mas não bloqueia o MVP funcional.

**Acceptance Criteria**:

1. WHEN `Settings()` é carregado com defaults THEN system SHALL expor `retrieval_hybrid_enabled=False`, `retrieval_hybrid_rrf_k=60` (gt=0), `retrieval_hybrid_sparse_top_k=20` (gt=0 e default ≥ `retrieval_top_k`) e `retrieval_sparse_stopwords` — cobertos em `tests/config/test_config.py` e documentados em `.env.example`. *(HYBR-21)*
2. WHEN `.env` define `RETRIEVAL_HYBRID_ENABLED=true` e demais variáveis híbridas THEN system SHALL refletir os valores em `Settings`. *(HYBR-22)*
3. WHEN `retrieval_hybrid_rrf_k=0` ou `retrieval_hybrid_sparse_top_k=-1` THEN system SHALL lançar erro de validação (fail-fast). *(HYBR-23)*
4. WHEN híbrido ativo e `_log_retrieve_metric` registra a métrica consolidada THEN system SHALL manter os campos atuais (`chunks`, `scores`, `latency_ms`, `cold_start`, `doc_types`, `failed_stores`) e adicionar campos aditivos `n_dense_candidates` e `n_sparse_candidates` (contagens de candidatos por caminho), mantendo `scores` como score final RRF — sem scores separados por chunk nesta versão (decisão Q8 — vinculante). Mudança aditiva que não quebra os testes de log existentes. *(HYBR-24)*

**Independent Test**: `pytest tests/config/test_config.py tests/retrieval/test_retriever.py` — defaults/constraints validados; record de log com híbrido contém contagens denso/esparso e os campos antigos permanecem.

---

## Edge Cases

- WHEN falha na construção do índice esparso THEN system SHALL seguir com dense-only (degradação graciosa, erro logado).
- WHEN falha na consulta esparsa (query time) THEN system SHALL seguir com dense-only, sem propagar exceção.
- WHEN hit apenas esparso (denso vazio pós-threshold L2) THEN system SHALL tratar como válido se a guarda lexical aprovar; senão, cold start.
- WHEN hit apenas denso (esparso vazio) THEN system SHALL preservar o comportamento atual.
- WHEN hits nos dois caminhos THEN system SHALL fundir via RRF com contribuições somadas por documento.
- WHEN empate de score RRF THEN system SHALL usar ordem determinística (estável; denso antes de esparso).
- WHEN o mesmo chunk é recuperado pelos dois caminhos THEN system SHALL retorná-lo uma única vez.
- WHEN novo documento (sha256 novo) é ingerido após o índice construído THEN system SHALL refletir a atualização na próxima query (índice invalidado/atualizado).
- WHEN coleções vazias THEN system SHALL retornar zero candidatos esparsos e cold start se denso também vazio.
- WHEN cold start é decidido pós-fusão e pós-guarda THEN system SHALL considerar: denso vazio + esparso com hits ≠ cold start; ambos vazios = cold start.
- WHEN query composta apenas de stopwords esparsas THEN system SHALL retornar zero tokens → zero candidatos esparsos → cair para dense-only.
- WHEN dosagens "10 mg" (com espaço) e "10mg" (sem espaço) aparecem THEN system SHALL normalizar removendo o espaço entre dígito e unidade e casá-las como o mesmo token `10mg` (decisão Q6 — vinculante).
- WHEN híbrido desabilitado com stores mockadas sem `_collection` (testes existentes) THEN system SHALL não acessar `_collection` nem o índice esparso.

---

## Requirement Traceability

| Requirement ID | Story | Phase | Status |
|----------------|-------|-------|--------|
| HYBR-01 | P1: Fusão híbrida | Design | Pending |
| HYBR-02 | P1: Fusão híbrida | Design | Pending |
| HYBR-03 | P1: Fusão híbrida | Design | Pending |
| HYBR-04 | P1: Fusão híbrida | Design | Pending |
| HYBR-05 | P1: Fusão híbrida | Design | Pending |
| HYBR-06 | P1: Fusão híbrida | Design | Pending |
| HYBR-07 | P1: Fusão híbrida | Design | Pending |
| HYBR-08 | P1: Fusão híbrida | Design | Pending |
| HYBR-09 | P1: Fusão híbrida | Design | Pending |
| HYBR-10 | P1: Fusão híbrida | Design | Pending |
| HYBR-11 | P1: Fusão híbrida | Design | Pending |
| HYBR-12 | P1: Índice BM25 | Design | Pending |
| HYBR-13 | P1: Índice BM25 | Design | Pending |
| HYBR-14 | P1: Índice BM25 | Design | Pending |
| HYBR-15 | P1: Índice BM25 | Design | Pending |
| HYBR-16 | P1: Índice BM25 | Design | Pending |
| HYBR-17 | P1: Índice BM25 | Design | Pending |
| HYBR-18 | P1: Índice BM25 | Design | Pending |
| HYBR-19 | P1: Índice BM25 | Design | Pending |
| HYBR-20 | P1: Índice BM25 | Design | Pending |
| HYBR-21 | P2: Config/Observabilidade | Design | Pending |
| HYBR-22 | P2: Config/Observabilidade | Design | Pending |
| HYBR-23 | P2: Config/Observabilidade | Design | Pending |
| HYBR-24 | P2: Config/Observabilidade | Design | Pending |

**ID format:** `HYBR-[NUMBER]` — prefixo curto do slug da feature.
**Coverage:** 24 total, 24 mapeados para tasks (T1–T10 em tasks.md), 0 unmapped ✅

---

## Data Model Changes

Sem modelos persistidos novos. Mudanças internas apenas:

- **Índice BM25 em memória** por DocType (corpus + frequências), sem tocar no ChromaDB (acesso read-only via `store._collection.get`); invalidação lazy por checagem de versão da coleção (count + hash agregado ou timestamp) a cada query quando o índice existe (decisão Q2).
- **Documento reconstruído no caminho esparso**: mesmo shape de `langchain_core.documents.Document` do caminho denso, com `page_content` e `metadata` idênticos ao armazenado no ChromaDB (`doc_type`, `source_path`, `sha256`, `chunk_index`, `char_count`, `page`, `section`).
- **Tokenização esparsa (decisão Q6)**: normalização dígito-unidade — "250mg" → token `250mg` íntegro; "10 mg" e "10mg" → mesmo token `10mg` (remoção de espaço dígito-unidade).
- **Observabilidade (decisão Q8)**: `scores` único permanece o score final RRF; campos aditivos `n_dense_candidates`/`n_sparse_candidates` no record de `_log_retrieve_metric`.
- Nenhuma migração; nenhuma mudança de schema da API.

## Process / Background Flow

**Happy path (híbrido ativo):**
1. `retrieve(query, stores, settings)` percorre o subset de stores (via `select_collections`).
2. Caminho denso (inalterado): `similarity_search_with_score(k=top_k)` por store, filtro L2 `score <= retrieval_score_threshold`.
3. Caminho esparso: índice BM25 lazy por DocType (construído do `store._collection`), busca por query tokenizada → `sparse_top_k` candidatos (Document reconstruído com metadados).
4. Dedup + fusão RRF (`k = retrieval_hybrid_rrf_k`): score = Σ 1/(k + rank) por lista, soma de contribuições do mesmo chunk.
5. Guarda lexical (`_lexical_relevance_guard`) — idêntica ao atual, agora sobre a lista fundida.
6. Rerank RAG-01 (se `retrieval_rerank_enabled`) — após a guarda, antes do corte.
7. Corte `[:retrieval_top_k]` → retorna `list[Document]`.

**Failure path — índice esparso indisponível:** construção/consulta esparsa falha → loga (logger.exception) e segue dense-only; contexto denso válido nunca é esvaziado.

**Failure path — cold start:** denso vazio E esparso vazio (ou eliminados pela guarda) → `[]`, `run_query` devolve `cold_start_message` sem chamar LLM.

## API Changes

Nenhuma mudança externa. `POST /query` (QueryRequest/QueryResponse), `POST /ingest` e `GET /health` permanecem inalterados. Mudança interna: branch híbrido dentro de `retrieve()` (funil único) — invariante AD-011 preservado (avaliação `_collect_rows` usa o mesmo caminho).

## Frontend Changes

Nenhuma mudança. UI (Streamlit) não é alterada — o híbrido é controlado por configuração de backend.

## Tests Required

**Unit:**
- `tests/config/test_config.py` (modify): defaults `retrieval_hybrid_*`, override por env, constraints `gt=0` (HYBR-21/22/23).
- `tests/retrieval/test_sparse.py` (new): tokenização (acentos, dosagens, stopwords esparsas), fórmula RRF, dedup cross-path, tie-break, reconstrução de metadados (HYBR-03/08/11/14/15/16/17).

**Integration (ChromaDB real em `tmp_path`, `_FakeEmbeddings`):**
- `tests/retrieval/test_sparse.py` (new): índice lazy por DocType, degradação graciosa, coleção vazia, refresh pós-ingest (HYBR-12/13/18/20).
- `tests/retrieval/test_retriever.py` (modify): identidade flag off, sparse-only ≠ cold start, cold start pós-fusão, guarda sobre esparso, isolamento per-DocType, top-k, contrato `list[Document]` (HYBR-01/02/04/05/06/07/10).

**Aceite:**
- `tests/acceptance/test_rag02_hybrid_search.py` (new, espelhando `test_rag01_reranking.py`): fluxo completo com flag on/off, RRF math, hit esparso gera citação válida via `build_citations`, invariante eval via `_collect_rows` (HYBR-01/03/05/09/17).

**Log:**
- `tests/retrieval/test_retriever.py` / `tests/acceptance/test_obs_01_logging.py` (modify): campos aditivos denso/esparso sem quebrar campos atuais (HYBR-24).

**Testes existentes que devem continuar verdes:** `tests/retrieval/test_retriever.py` (745 linhas, inclui mocks `MagicMock(spec=Chroma)` sem `_collection`), `tests/retrieval/test_reranker.py`, `tests/acceptance/test_rag01_reranking.py`, `tests/acceptance/test_obs_01_logging.py`, `tests/config/test_config.py`, `tests/evaluation/test_metrics.py`.

## Files That Will Change

| File | Change type | Why |
|------|-------------|-----|
| `src/medasist/retrieval/sparse.py` | New | Índice BM25 lazy por DocType + tokenizador + busca esparsa |
| `src/medasist/retrieval/retriever.py` | Modify | Branch híbrido flag-gated dentro de `retrieve()` + fusão RRF + métrica aditiva |
| `src/medasist/config.py` | Modify | Campos `retrieval_hybrid_*` + `retrieval_sparse_stopwords` (padrão bloco # Retrieval) |
| `.env.example` | Modify | Documentar novas env vars híbridas |
| `requirements.txt` | Modify | Declarar biblioteca BM25 (base) |
| `requirements-api.txt` | Modify | Declarar biblioteca BM25 (runtime API — lacuna RAG-01 não repetida) |
| `src/medasist/retrieval/__init__.py` | Modify (opcional) | Re-exportar API esparsa (padrão `__all__`) |
| `tests/retrieval/test_sparse.py` | New | Unit + integration do módulo esparso |
| `tests/retrieval/test_retriever.py` | Modify | Testes do branch híbrido no funil |
| `tests/config/test_config.py` | Modify | Testes das novas settings |
| `tests/acceptance/test_rag02_hybrid_search.py` | New | Aceite end-to-end do híbrido |

## Risks

- **Semântica de cold start (decisão de segurança médica):** hit apenas esparso passa a ser válido (HYBR-05). Risco de responder com base só em keyword match; mitigado pela guarda lexical obrigatória (HYBR-07) e decisão vinculante Q3 (confirmada na story aprovada).
- **Corpus inconsistente em acentos** ("bulas sem acentos, diretrizes/protocolos com acentos"): mitigado por normalização de diacríticos na tokenização esparsa (HYBR-14).
- **BM25 não normalizado:** corte por score esparso difícil de calibrar; risco de derrubar match exato — mitigação: top-k sem corte (decisão vinculante Q5).
- **Índice em memória desatualizado após `/ingest`:** risco de chunks novos invisíveis ao esparso — mitigação: rebuild lazy por checagem de versão da coleção (count + hash agregado ou timestamp) a cada query quando o índice existe (decisão vinculante Q2).
- **Testes existentes com `MagicMock(spec=Chroma)` sem `_collection`:** risco de quebra — mitigado por flag default off + lazy import (HYBR-01; mesma estratégia do RAG-01).
- **Footprint de memória do índice em memória:** corpus inteiro em RAM por DocType; aceitável para corpus local, monitorar em produção.
- **Thread-safety do singleton lazy:** construção concorrente na primeira query — mitigado por double-checked locking (padrão reranker, HYBR-12).
- **Divergência do caminho de avaliação (AD-011):** qualquer lógica híbrida fora de `retrieve()` quebraria o eval — mitigado por HYBR-09.
- **Guarda lexical com falso-negativo:** mesmo fármaco com nomes distintos (paracetamol vs acetaminofeno) vira cold start — comportamento seguro e já documentado em AD-012 (aceito).
- **Escolha de dependência:** `rank_bm25` (pura-Python) decidida e vinculante (Q1); pinagem e localização (requirements.txt + requirements-api.txt) obrigatórias (HYBR-19; lacuna RAG-01 não repetida).

## Decisões Resolvidas (Q1–Q8 — vinculantes)

| # | Pergunta | Decisão (vinculante) |
|---|----------|----------------------|
| Q1 | Biblioteca esparsa | **`rank_bm25`** (pura-Python, sem dependências pesadas) como única dependência nova, declarada em `requirements.txt` E `requirements-api.txt` (HYBR-19). |
| Q2 | Invalidação do índice após `/ingest` | Rebuild **lazy** por checagem de versão da coleção (count de documentos + hash agregado ou timestamp) a cada query quando o índice existe; **sem hook no route `/ingest`** (evita divergir do caminho de avaliação AD-011). |
| Q3 | Cold start com hit apenas esparso | **JÁ CONFIRMADO na story aprovada (AC7):** hit apenas esparso com guarda lexical satisfeita **NÃO é cold start** (HYBR-05). |
| Q4 | Pesos denso/esparso na fusão | **RRF clássico sem pesos** — `retrieval_hybrid_rrf_k` único (default 60). Sem pesos configuráveis nesta versão. |
| Q5 | Threshold de score esparso | **Nenhum corte BM25** — apenas `retrieval_hybrid_sparse_top_k` limita candidatos esparsos. |
| Q6 | Normalização de dosagens | Tokenização preserva dígito-unidade sem espaço ("250mg" → token `250mg`); e também casa formas com espaço via normalização que remove espaços entre dígito e unidade ("10 mg" e "10mg" → mesmo token `10mg`). |
| Q7 | Default `retrieval_hybrid_sparse_top_k` | **20** (≥ `retrieval_top_k`=10, precedente RAG-01). |
| Q8 | Observabilidade | Manter campo único `scores` (score final RRF) + contagens de candidatos por caminho (`n_dense_candidates`, `n_sparse_candidates`) como campos aditivos em `_log_retrieve_metric`; **sem scores separados por chunk nesta versão**. |

---

## Open Questions

None. (Q1–Q8 resolvidas e vinculantes; registradas na tabela acima.)

---

## Success Criteria

- [ ] Com `retrieval_hybrid_enabled=False` (default), a suíte existente passa integralmente e `retrieve()` é idêntico ao atual (sem acesso ao índice esparso).
- [ ] Com flag ativa, query por nome exato de medicamento com denso vazio retorna o chunk esparso e `run_query` gera resposta com citação válida (não cold start).
- [ ] `tests/retrieval/test_sparse.py`, `tests/acceptance/test_rag02_hybrid_search.py` e os demais testes novos passam; cobertura ≥ 80% no gate full.
- [ ] `build_citations` e `_lexical_relevance_guard` produzem os mesmos resultados para chunks vindos do esparso e do denso.
- [ ] Avaliação RAG (`_collect_rows`) usa o caminho híbrido idêntico à API (AD-011) — sem lógica híbrida fora de `retrieve()`.
- [ ] Dependência BM25 declarada em `requirements.txt` e `requirements-api.txt` (lacuna RAG-01 não repetida).