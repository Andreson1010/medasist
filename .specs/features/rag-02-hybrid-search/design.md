# RAG-02 — Hybrid Search (denso + esparso) — Design

**Spec:** `.specs/features/rag-02-hybrid-search/spec.md`
**Status:** Awaiting human approval

---

## Architecture Overview

O híbrido vive **dentro do funil único `retrieve()`** (invariante AD-011 — avaliação usa o mesmo caminho). Um novo módulo `src/medasist/retrieval/sparse.py` mantém, por DocType, um índice BM25 **em memória**, construído **lazy** a partir da coleção ChromaDB (`store._collection.get(include=["documents","metadatas"])` — mesmo acesso do probe de avaliação). O `retrieve()` ganha um branch flag-gated (`retrieval_hybrid_enabled`, default off → identidade total).

Pipeline com híbrido ativo:

```mermaid
graph TD
    Q[Query do usuário] --> R[retrieve - funil único]
    R --> D[Path denso: similarity_search_with_score por store<br/>filtro L2 <= threshold]
    R --> S[Path esparso: SparseIndex BM25 lazy por DocType<br/>top-k esparso]
    D --> F[RRF fuse: dedup + score = Σ 1/(k+rank)]
    S --> F
    F --> G[Guarda lexical _lexical_relevance_guard]
    G --> RR[Rerank RAG-01 se habilitado]
    RR --> C[Corte [:retrieval_top_k] → list[Document]]
    C --> CH[chain / run_query / eval _collect_rows]
```

Decisões-chave (detalhadas em Tech Decisions):

1. **Flag default off + lazy import** → comportamento atual preservado e testes existentes (`MagicMock(spec=Chroma)` sem `_collection`) continuam verdes — mesma estratégia do RAG-01.
2. **Fusão RRF com k=60, sem pesos** (padrão da literatura) — ordem: denso → esparso → dedup → RRF → guarda → rerank → corte.
3. **Cold start pós-fusão e pós-guarda** — denso vazio + esparso com hits aprovados pela guarda ≠ cold start (decisão de segurança médica, HYBR-05).
4. **Tokenização esparsa normalizada** (minúsculas + remoção de diacríticos, dosagens dígito-unidade íntegras, stopwords esparsas separadas — `mg/ml/g/kg` preservados).
5. **Degradação graciosa** — qualquer falha esparsa loga e cai para dense-only; nunca propaga, nunca esvazia contexto denso válido (padrão reranker).

## Code Reuse Analysis

### Existing Components to Leverage

| Component | Location | How to Use |
|-----------|----------|------------|
| Funil `retrieve()` (L117–239) | `src/medasist/retrieval/retriever.py` | Estender com branch híbrido flag-gated; manter dedup/guarda/corte existentes |
| Singleton lazy thread-safe (double-checked locking) + degradação graciosa | `src/medasist/retrieval/reranker.py` | Aplicar o mesmo padrão ao `SparseIndex` (construção única, falha → input inalterado) |
| `select_collections` (L62–88) | `src/medasist/retrieval/retriever.py` | Caminho esparso consulta apenas o subset de stores selecionado (HYBR-10) |
| `_lexical_relevance_guard` (L242–287) | `src/medasist/retrieval/retriever.py` | Reutilizado **inalterado** sobre a lista fundida (lê `page_content` + `source`/`source_path`) |
| `_log_retrieve_metric` (L320–366) | `src/medasist/retrieval/retriever.py` | Estender de forma aditiva (HYBR-24) |
| `rerank_documents` plug (L215–216) | `src/medasist/retrieval/retriever.py` | Mantido após a guarda, sobre a lista fundida |
| `build_citations` | `src/medasist/generation/citations.py` | Inalterado — exige metadados fiéis nos Documents esparsos (HYBR-17) |
| Acesso read-only ao corpus da coleção | `store._collection.get(...)` (padrão do probe em `scripts/evaluate_rag.py`) | Snapshot do corpus + metadados para o índice BM25 |
| Padrão de config `Field(default=..., gt=0)` + `.env.example` + testes | `src/medasist/config.py`, `tests/config/test_config.py` | Novos campos `retrieval_hybrid_*` seguem o mesmo padrão (HYBR-21/22/23) |

### Integration Points

| System | Integration Method |
|--------|--------------------|
| ChromaDB (coleção por DocType) | Leitura read-only via `store._collection.get(include=["documents","metadatas"])` — sem escrita, sem migração |
| `POST /query` → `run_query` | Nenhuma mudança de contrato; híbrido transparece via `retrieve()` |
| Avaliação RAG (`_collect_rows` → `retrieve()`) | Caminho idêntico à API (AD-011, HYBR-09) |
| Rerank RAG-01 | Chamado depois da guarda lexical, sobre a lista fundida |
| `/ingest` | Efeito indireto: novos chunks devem ser refletidos no índice (HYBR-18 — decisão Q2: rebuild lazy por checagem de versão da coleção) |

---

## Components

### `SparseIndex` (novo — `src/medasist/retrieval/sparse.py`)

- **Purpose**: Índice BM25 em memória por DocType, lazy, thread-safe, com busca esparsa que devolve `Document`s reconstruídos com metadados fiéis.
- **Location**: `src/medasist/retrieval/sparse.py`
- **Interfaces**:
  - `get_sparse_index(store: Chroma, settings: Settings) -> SparseIndex` — singleton lazy por coleção (double-checked locking; cache com hook de reset para testes).
  - `SparseIndex.search(query: str, top_k: int) -> list[tuple[Document, float]]` — devolve pares (Document reconstruído, score BM25) ordenados por score decrescente.
  - `SparseIndex.is_stale() -> bool` — checagem de refresh (ex.: `_collection.count()` mudou desde a construção).
  - `reset_sparse_indexes() -> None` — hook de teste/refresh (mitiga CONCERNS L4 — singleton vazando entre testes).
- **Dependencies**: `rank_bm25` (decisão Q1 — vinculante; pura-Python, única dependência nova), `store._collection` (read-only), `Settings`.
- **Reuses**: padrão de singleton lazy + degradação graciosa de `reranker.py`; snapshot do corpus no padrão do probe de avaliação.

### `_SparseTokenizer` (privado — `src/medasist/retrieval/sparse.py`)

- **Purpose**: Tokenização normalizada para BM25: minúsculas, remoção de diacríticos (acentos), tokens dígito-unidade íntegros ("500mg"), normalização dígito-unidade com espaço ("10 mg" → `10mg`, decisão Q6), stopwords esparsas separadas (sem `mg/ml/g/kg`).
- **Location**: `src/medasist/retrieval/sparse.py`
- **Interfaces**:
  - `tokenize(text: str, settings: Settings) -> list[str]` — tokens normalizados.
- **Dependencies**: `Settings.retrieval_sparse_stopwords`; regex local (padrão `_TOKEN_RE` do retriever).
- **Reuses**: convenção de regex de tokens do `retriever.py` (`_TOKEN_RE`), estendida para dosagens dígito-unidade.

### Fusão RRF (privado — `src/medasist/retrieval/retriever.py` ou `sparse.py`)

- **Purpose**: Fundir listas densa e esparsa com RRF, deduplicando por `page_content` e somando contribuições do mesmo chunk; empate determinístico (denso precede esparso).
- **Location**: `src/medasist/retrieval/retriever.py` (junto ao funil; ou reexportado de `sparse.py`)
- **Interfaces**:
  - `_rrf_fuse(dense: list[tuple[Document, float]], sparse: list[tuple[Document, float]], k: int) -> list[tuple[Document, float]]` — lista ordenada por score RRF desc.
- **Dependencies**: nenhuma externa.
- **Reuses**: dedup por `page_content` (padrão atual em `retrieve()`), ordenação estável (padrão reranker).

### Branch híbrido em `retrieve()` (modify — `src/medasist/retrieval/retriever.py`)

- **Purpose**: Orquestrar dense + sparse + fusão + guarda + rerank + corte, preservando contrato e flag-off identity.
- **Location**: `src/medasist/retrieval/retriever.py` (`retrieve`, L117–239)
- **Interfaces**:
  - `retrieve(query, stores, settings) -> list[Document]` — assinatura inalterada (HYBR-02).
  - Interno: `_hybrid_candidates(query, stores, settings) -> list[tuple[Document, float]]` — dense (filtrado L2) + sparse (top-k) não deduplicados, com scores por caminho.
- **Dependencies**: `SparseIndex`, `_rrf_fuse`, `_lexical_relevance_guard`, `rerank_documents`.
- **Reuses**: todo o funil atual; rework do early-return de cold start (L177) para decidir **pós-fusão** quando flag ativa.

---

## Data Models

### Documento reconstruído no caminho esparso

Mesmo contrato de `langchain_core.documents.Document` do caminho denso. Fidelidade de metadados (HYBR-17):

| Campo | Origem no ChromaDB | Consumido por |
|-------|--------------------|---------------|
| `page_content` | `documents` da coleção | `_lexical_relevance_guard`, `_format_context`, RRF dedup |
| `metadata.doc_type` | `metadatas.doc_type` | testes/relatórios |
| `metadata.source_path` | `metadatas.source_path` | `build_citations` (fallback `source`), guarda lexical |
| `metadata.sha256` | `metadatas.sha256` | rastreabilidade |
| `metadata.chunk_index` | `metadatas.chunk_index` | rastreabilidade |
| `metadata.page` | `metadatas.page` (0 = sentinela) | `build_citations` (0 → vazio, como no denso) |
| `metadata.section` | `metadatas.section` | `build_citations` |
| `metadata.char_count` | `metadatas.char_count` | paridade com denso |

**Relationships**: 1 índice BM25 ↔ 1 coleção ChromaDB ↔ 1 DocType. Sem persistência, sem migrations.

---

## Error Handling Strategy

| Error Scenario | Handling | User Impact |
|----------------|----------|-------------|
| Falha ao construir índice esparso (collection.get, import BM25) | `logger.exception` + flag interno "sparse indisponível" → dense-only | Nenhum; resposta idêntica ao modo atual |
| Falha ao consultar índice esparso (query time) | `logger.exception` → dense-only | Nenhum; resposta idêntica ao modo atual |
| Coleção vazia | Zero candidatos esparsos, sem erro | Cold start se denso também vazio |
| Índice desatualizado após `/ingest` | Rebuild lazy por checagem de versão da coleção (count + hash agregado ou timestamp) a cada query quando o índice existe (decisão Q2) | Chunks novos visíveis ao esparso na query seguinte |
| Guarda lexical esvazia lista fundida | Cold start (segurança médica, idêntico ao atual) | Mensagem fixa `cold_start_message` |

**Regra transversal:** o caminho esparso **nunca** propaga exceção para `retrieve()` e **nunca** esvazia um contexto denso válido (mesma garantia do reranker RAG-01).

---

## Tech Decisions (only non-obvious ones)

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Biblioteca BM25 | **`rank_bm25`** (decisão Q1 — vinculante) | Pura-Python, sem dependências pesadas, suficiente para BM25 clássico; única dependência nova, pinada em `requirements.txt` + `requirements-api.txt` (HYBR-19) |
| Flag de ativação | `retrieval_hybrid_enabled=False` default | Identidade total e testes existentes verdes (HYBR-01); opt-in como RAG-01 |
| Fusão | RRF com `k=60`, sem pesos (decisão Q4 — vinculante) | RRF clássico sem pesos configuráveis; determinístico; sem superfície extra de calibração |
| `sparse_top_k` default | **20** (`gt=0`, ≥ `retrieval_top_k`) | Precedente `rerank_top_n` do RAG-01 (decisão Q7 — vinculante) |
| Tokenização esparsa | Normalização de acentos + dosagens dígito-unidade íntegras + normalização de espaço dígito-unidade ("10 mg"/"10mg" → `10mg`, decisão Q6) + stopwords esparsas separadas | Corpus inconsistente em acentos; `mg/ml/g/kg` não podem ser descartados (HYBR-14/15/16) |
| Invalidação do índice | Rebuild lazy por checagem de versão da coleção (count + hash agregado ou timestamp) a cada query quando o índice existe (decisão Q2) | Mantém lógica dentro de `retrieve()` (AD-011); sem hook no route `/ingest` |
| Cold start | Decidido pós-fusão e pós-guarda (decisão Q3 — confirmada na story aprovada) | Hit esparso exato com guarda satisfeita NÃO é cold start (HYBR-05) |
| Observabilidade | `scores` único (score final RRF) + campos aditivos `n_dense_candidates`/`n_sparse_candidates` (decisão Q8) | Compat com `test_obs_01_logging.py` e `test_retriever.py`; sem scores separados por chunk nesta versão (HYBR-24) |
| Sem corte de score esparso | Apenas `sparse_top_k` (decisão Q5 — vinculante) | BM25 não é normalizado; corte derrubaria match exato |

## Mitigations for CONCERNS.md

| Concern | Severity | Mitigation no design |
|---------|----------|----------------------|
| M7 — broad `except Exception` no retriever engole erros de programação | MEDIUM | Capturas esparsas confinadas ao módulo `sparse.py`, com exceções específicas propagadas em dev e degradação graciosa em runtime (padrão reranker) |
| L4 — singleton de settings nunca resetado (isolamento de testes) | LOW | `reset_sparse_indexes()` exposto e usado em fixtures — sem vazamento entre testes |
| H6 — `doc_types` filter | HIGH (já corrigido, AD-004) | Preservar: esparso consulta apenas o subset de `select_collections` (HYBR-10) |
| M3 — section/page em citações | MEDIUM (corrigido em RAG-04/AD-012) | Preservar: Documents esparsos carregam `section`/`page` do ChromaDB (HYBR-17) |
| M4/M5 — duplicação de código | MEDIUM | Sem nova duplicação: `sparse.py` centraliza acesso ao índice; snapshot do corpus usa o padrão existente do probe |
| Dependências não pinadas (langchain-text-splitters etc.) | MEDIUM | Nova dependência BM25 declarada **explícita** em `requirements.txt` + `requirements-api.txt` (HYBR-19) — lacuna RAG-01 não repetida |
| L1 — config sem teste dedicado | LOW | Testes de config do híbrido em `tests/config/test_config.py` (precedente RAG-01/AD-013) |

---

## Uncertainties

As 8 perguntas foram **resolvidas como decisões vinculantes** no `spec.md` (tabela "Decisões Resolvidas (Q1–Q8 — vinculantes)") e estão refletidas nas Tech Decisions acima — **nenhuma decisão de implementação fica pendente de humano**:

- **Q1**: `rank_bm25` (única dependência nova, em `requirements.txt` + `requirements-api.txt`).
- **Q2**: rebuild lazy por checagem de versão da coleção (count + hash agregado ou timestamp) a cada query quando o índice existe; sem hook no route `/ingest`.
- **Q3**: hit apenas esparso com guarda lexical satisfeita NÃO é cold start (confirmado na story aprovada).
- **Q4**: RRF clássico sem pesos, `retrieval_hybrid_rrf_k=60`.
- **Q5**: sem corte de score BM25; apenas `retrieval_hybrid_sparse_top_k` limita candidatos.
- **Q6**: normalização dígito-unidade — "250mg" → token `250mg`; "10 mg"/"10mg" → mesmo token `10mg`.
- **Q7**: `retrieval_hybrid_sparse_top_k=20` (default ≥ `retrieval_top_k`).
- **Q8**: `scores` único (score final RRF) + campos aditivos `n_dense_candidates`/`n_sparse_candidates`; sem scores separados por chunk nesta versão.

Incertezas residuais são empíricas (calibração fina de `sparse_top_k`, proporção ideal esparso:denso) e serão resolvidas com dados de avaliação após a implementação — não bloqueiam o design nem as tasks.