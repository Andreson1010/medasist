# RAG-01: Reranking Cross-Encoder — Design

**Spec:** `.specs/features/rag-01-reranking/spec.md`
**Status:** Awaiting human approval

---

## Architecture Overview

O reranker pluga-se **dentro do funil único `retrieve()`** (`src/medasist/retrieval/retriever.py`), como um estágio de reordenação entre o guarda lexical e o corte final `retrieval_top_k`. Um novo módulo `reranker.py` encapsula o helper de reordenação e o singleton lazy thread-safe do `CrossEncoder`. A assinatura pública `retrieve(...) -> list[Document]` e o contrato da chain (`run_query`) permanecem inalterados, preservando o invariante AD-011 (contexts do eval idênticos aos da resposta).

```mermaid
graph TD
    A[retrieve query, stores, settings] --> B[por store: similarity_search_with_score]
    B --> C[filtrar threshold L2]
    C -->|vazio| CS[Cold start: retorna []]
    C --> D[dedup por page_content + sort L2]
    D --> G[_lexical_relevance_guard]
    G -->|vazio| CS
    G --> R{rerank_enabled?}
    R -->|no| OUT[guarded[:k] -> list Document]
    R -->|yes| RK[rerank_documents guarded, query]
    RK -->|falha| OUT
    RK -->|ok| OUT2[reordenar por score desc, corte top_k -> list Document]
    OUT --> RET[retorno list[Document]]
    OUT2 --> RET

    subgraph RERANKER["reranker.py"]
        RK --> S[singleton lazy CrossEncoder]
        S --> P[predict batch sobre rerank_top_n]
    end
```

### Novo fluxo de `retrieve()`

| Passo | Comportamento atual | Comportamento com rerank |
|-------|---------------------|--------------------------|
| Coletar candidatos | `similarity_search_with_score` por store | Inalterado |
| Filtro threshold L2 | `score <= retrieval_score_threshold` | Inalterado (cold start decidido AQUI, pré-rerank) |
| Cold start | retorna `[]` | Inalterado; reranker não chamado |
| Dedup + sort L2 | dedup por `page_content`, sort por distância | Inalterado |
| Guarda lexical | `_lexical_relevance_guard` | Inalterado (binário, barato) |
| Reordenação | `guarded[:k]` | **Novo:** se habilitado e `guarded` não-vazio → `rerank_documents(...)`, depois corte `[:k]` |
| Retorno | `list[Document]` | Inalterado (contrato público) |

---

## Code Reuse Analysis

### Existing Components to Leverage

| Component | Location | How to Use |
|-----------|----------|------------|
| `store.get_client` singleton pattern | `src/medasist/vectorstore/store.py:24-65` | Replicar o double-checked locking (`threading.Lock`) para o singleton do `CrossEncoder`. |
| `PromptRegistry` lazy + thread-safe | `src/medasist/generation/prompts.py:12-60` | Padrão de cache lazy guardado por lock — aplicado ao carregamento do modelo. |
| `_log_retrieve_metric` | `src/medasist/retrieval/retriever.py:312-358` | Reutilizar para log da latência e contagem; adicionar campo opcional de rerank. |
| `_lexical_relevance_guard` | `src/medasist/retrieval/retriever.py:234-279` | Ponto de ancoragem: rerank roda **depois** dele (Q6). |
| `Field(default=..., gt=0)` pattern | `src/medasist/config.py` | Novas settings seguem o mesmo padrão de constraints. |

### Integration Points

| System | Integration Method |
|--------|--------------------|
| sentence-transformers `CrossEncoder` | Importado lazy dentro de `reranker.py`; `predict([(query, doc.page_content)])` retorna scores (maior = mais relevante). |
| LangChain `Document` | Rerank opera sobre `list[tuple[Document, float]]` (Document + L2 score); reordena e retorna o mesmo tipo. |
| Avaliação RAGAS | `_collect_rows` já chama `retrieve()` — herda o rerank automaticamente (invariante AD-011). |

---

## Components

### `src/medasist/retrieval/reranker.py` (novo)

- **Purpose**: Encapsular o carregamento lazy e thread-safe do `CrossEncoder` e a reordenação dos candidatos.
- **Location**: `src/medasist/retrieval/reranker.py`
- **Interfaces**:
  - `rerank_documents(docs: list[tuple[Document, float]], query: str, settings: Settings) -> list[tuple[Document, float]]` — reordena por score do reranker (desc), tie-break determinístico pela ordem L2 original; em falha, retorna `docs` inalterados (ordem L2) e loga.
  - `_get_reranker(settings: Settings) -> CrossEncoder` (privado) — singleton lazy com `threading.Lock` + double-checked locking; retorna o modelo ou lança (a falha é tratada por `rerank_documents`).
  - `_rank(docs, query, settings) -> list[float]` (privado) — monta pares `(query, doc.page_content)` e chama `predict` em batch (uma chamada sobre até `rerank_top_n` pares).
- **Dependencies**: `sentence-transformers` (`CrossEncoder`), `Settings`, `langchain_core.documents.Document`.
- **Reuses**: padrão singleton de `store.get_client`; convenções de logging e docstrings NumPy PT-BR.

### `src/medasist/retrieval/retriever.py` (modificar)

- **Purpose**: Plug do estágio de rerank em `retrieve()`.
- **Location**: `src/medasist/retrieval/retriever.py` (`retrieve`, linha ~208 entre guarda lexical e `guarded[:k]`).
- **Interfaces**: `retrieve(query, stores, settings) -> list[Document]` — inalterada.
- **Dependencies**: importa `rerank_documents` de `reranker.py` (lazy ou top-level).
- **Reuses**: `_lexical_relevance_guard`, `_log_retrieve_metric`.

### `src/medasist/config.py` (modificar)

- **Purpose**: 4 novas settings no bloco `# Retrieval`.
- **Location**: `src/medasist/config.py` (~linha 372, após `retrieval_drug_term_min_len`).
- **Interfaces**: campos `Settings` — `retrieval_rerank_enabled` (bool, default False), `retrieval_rerank_model` (str, default `BAAI/bge-reranker-base`), `retrieval_rerank_top_n` (int, `gt=0`, default 20), `retrieval_rerank_batch_size` (int, `gt=0`, default 16).

### `src/medasist/evaluation/metrics.py` (modificar)

- **Purpose**: Adicionar MRR customizado à avaliação RAGAS.
- **Location**: `src/medasist/evaluation/metrics.py`
- **Interfaces**:
  - `_reciprocal_rank(contexts: list[str], reference_contexts: list[str]) -> float` (privado) — 1/rank do primeiro `reference_context` encontrado em `contexts` (0 se nenhum).
  - `_aggregate_mrr(rows, eval_indices) -> float | None` (privado) — média do RR sobre o subconjunto não-cold-start.
- **Reuses**: `_collect_rows` (já usa `retrieve()`); `QuestionEvalRow`/`EvaluationReport` ganham o campo MRR opcional em `metrics`/`aggregates`.

---

## Data Models (if applicable)

### Settings (config.py) — novas

```python
retrieval_rerank_enabled: bool = Field(default=False)
retrieval_rerank_model: str = Field(default="BAAI/bge-reranker-base")
retrieval_rerank_top_n: int = Field(default=20, gt=0)
retrieval_rerank_batch_size: int = Field(default=16, gt=0)
```

**Relationships**: consumidas apenas por `reranker.py` (via `settings` injetado em `retrieve`). Sem migração de banco.

### EvaluationReport (metrics.py) — extensão

- `aggregates` ganha chave `mrr: float | None`.
- `QuestionEvalRow.metrics` ganha chave opcional `mrr: float | None`.

---

## Error Handling Strategy

| Error Scenario | Handling | User Impact |
|----------------|----------|-------------|
| `CrossEncoder.predict` lança (erro/timeout/modelo ausente) | `rerank_documents` captura, `logger.exception`, retorna docs na ordem L2 | Nenhum — resposta segue na ordem L2 (degradação graciosa) |
| Modelo não baixado / sem rede no primeiro uso | Lazy load lança; capturado pelo mesmo `try/except` → ordem L2 | Nenhum — identidade |
| Cold start (nenhum candidato L2) | `retrieve()` retorna `[]` antes do rerank | Cold start normal, mensagem fixa |
| Cold start lexical (guarda esvazia) | `retrieve()` retorna `[]` antes do rerank | Cold start normal |
| `rerank_top_n` <= 0 em runtime (config) | `Field(gt=0)` impede na construção do Settings | Config rejeitada no startup |

---

## Tech Decisions (only non-obvious ones)

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Q1 Runtime | sentence-transformers `CrossEncoder` (BAAI/bge-reranker-base) | Cross-encoder real de rerank; LM Studio não tem endpoint de rerank. |
| Q3 Default flag | `retrieval_rerank_enabled=False` | Preserva comportamento atual; CI/testes sem dependência do modelo; opt-in explícito. |
| Q4 Score semantics | Rerank substitui a ordenação L2; cold start decidido PRÉ-rerank no L2 | Rerank nunca esvazia contexto válido (regra de segurança médica); sem gate de cold start por score do reranker. |
| Q5 `rerank_top_n` | 20 (>= `retrieval_top_k`) | Cobre candidatos típicos (até 4 stores) com margem; limita custo da hot path. |
| Q6 Ordem rerank vs guarda lexical | Rerank DEPOIS do guarda lexical | Guarda é binário e barato; evita pontuar candidatos que serão descartados; cold start lexical preservado. |
| Q8 Lifecycle modelo | Lazy singleton thread-safe (double-checked locking), carregado na 1ª chamada, não no lifespan | Evita carga pesada no startup e regressão nos testes de API (que mockam `build_chain`); testes mockam `CrossEncoder`. |
| Determinismo | Tie-break pela ordem L2 original (índice estável) | Ordenação determinística, coberta por teste (edge case). |

---

## Mitigations for CONCERNS.md Items

- **M7 (broad `except Exception` no retriever):** o try/except do rerank fica isolado no helper `rerank_documents`, capturando apenas a falha do reranker e reutilizando `logger.exception` (padrão existente). A falha nunca propaga para `retrieve()`; não introduz novos catches amplos no corpo principal de `retrieve()`.
- **L4 (settings singleton nunca resetado):** testes das novas settings usam `Settings` instanciada com env (via fixture) — não dependem de `get_settings()` para validar defaults; nenhuma mutação de singleton introduzida.
- **L1 (test coverage gaps — config):** a adição das novas settings é acompanhada de testes dedicados (`tests/config/test_config.py`), reduzindo a lacuna conhecida.

---

## Open Questions

None — Q1–Q8 resolvidas e registradas em `spec.md`.
