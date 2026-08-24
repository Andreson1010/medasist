# RAG-03 — Decomposição de perguntas multi-parte — Design

**Spec:** `.specs/features/rag-03-multipart/spec.md`
**Status:** Awaiting human approval

---

## Architecture Overview

RAG-03 adiciona decomposição multi-parte ao pipeline RAG **no layer `generation`/`retrieval`**, de modo que `run_query` (síncrono), `stream_answer` (streaming) e `_collect_rows` (avaliação) percorram o **mesmo caminho** (invariante AD-011 / MP-12 / MP-14). A lógica de **split** (decidir se é composta e dividir via LLM local) vive num módulo novo **`retrieval/decompose.py`** (análogo a `retrieval/query_rewrite.py`); a lógica de **orquestração + merge** (rodar cada sub-pergunta pelo funil, re-numerar citações e remapear `[N]`) vive em **`generation/chain.py`**, com um helper de remapeamento em **`generation/citations.py`**.

A feature é **off por padrão** (`retrieval_decompose_enabled=False`): quando off, `decompose_query` retorna `[question]` e `run_query`/`stream_answer` seguem exatamente o caminho atual (byte-identical). Quando on, uma pergunta composta é decomposta e cada sub-pergunta passa pelo funil `retrieve()` independente (threshold L2 + guarda lexical + reescrita curta por sub), e as respostas são re-combinadas numa resposta única.

```mermaid
graph TD
    Q[question] --> D[generation.chain run_query/stream_answer]
    D --> DEC[retrieval.decompose decompose_query]
    DEC -->|flag off / não-composta / falha / 0-1 sub| ID[return [question] — identidade]
    DEC -->|composta| SPLIT[ChatOpenAI lazy split → list[str], cap=5]
    SPLIT --> SUBS[sub1, sub2, ...]
    SUBS --> RUN[para cada sub: _run_single via retrieve()]
    RUN --> RET[retrieve sub: rewrite curta + L2 threshold + guarda lexical]
    RET --> CS[sub miss → is_cold_start]
    RET --> CIT[sub hit → build_citations + geração + validate_citations]
    CIT --> SUBRES[(sub answer, citations, is_cold_start)]
    SUBRES --> MERGE[generation.chain _merge_sub_results]
    MERGE --> RENUM[citações re-numeradas 1-based + [N] remapeados via citations.remap_answer]
    MERGE --> PARTIAL[unanswered_sub_questions = misses]
    MERGE -->|nenhuma citação válida| TOTAL[GenerationResult cold start total]
    MERGE -->|≥1 citação válida| OK[GenerationResult merged is_cold_start=False]
    OK --> API[QueryResponse + unanswered_sub_questions field aditivo]
    OK --> SSE[stream_answer deltas + citations re-numeradas]
```

### Fluxo por camada

| Camada | Responsabilidade |
|--------|------------------|
| `retrieval/decompose.py` | `decompose_query(query, settings) -> list[str]` — gate `_is_compound` (Q4), split via `ChatOpenAI` lazy, cap `max_sub_questions`, degradação para `[question]`. Nada sabe de geração/merge. |
| `generation/chain.py` | `run_query`/`stream_answer` chamam `decompose_query`; se `len>1`, rodam cada sub pelo funil e chamam `_merge_sub_results`; se `len==1`, usam `_run_single` (identidade byte-identical quando flag off). |
| `generation/citations.py` | `remap_answer(answer, offset) -> str` — shift de marcadores `[N]` por offset acumulado (reuso em qualquer merge). |
| `api/schemas.py` | `QueryResponse.unanswered_sub_questions` (aditivo, default `[]`) + mapeamento em `from_result`. |
| `config.py` | Campos `retrieval_decompose_*` + resolução do modelo vazio em `_resolve_eval_models`. |

---

## Sync-Route + Reuse Model

- `run_query` e `stream_answer` permanecem síncronos (retriever/ChromaDB/LLM síncronos — L-004/FIX-02). Nenhuma mudança de async.
- O layer de **split** reusa exatamente o padrão de `query_rewrite.py`: `ChatOpenAI = None` module-level + import `langchain_openai` dentro de `_split` (permite `patch("medasist.retrieval.decompose.ChatOpenAI")`), `_TOKEN_RE` local para evitar import circular, prompt module-level, degradação com `logger.exception`.
- O **merge** reusa `build_citations`/`validate_citations` (por sub) e o novo `remap_answer` (por sub no merge) — sem duplicar lógica de citação.

---

## Code Reuse Analysis

### Existing Components to Leverage

| Component | Location | How to Use |
|-----------|----------|------------|
| `retrieve()` (funil único: L2 + guarda lexical + rerank + híbrido + reescrita) | `src/medasist/retrieval/retriever.py` | Cada sub-pergunta passa por `retrieve(sub, subset, settings)` — reuso verbatim (MP-02/MP-06) |
| `rewrite_query` (RAG-03 curta) | `src/medasist/retrieval/query_rewrite.py` | Já é chamado dentro de `retrieve()` — reescrita curta por sub-pergunta automática (MP-06) |
| `build_citations` / `validate_citations` | `src/medasist/generation/citations.py` | Reusados por sub (MP-03/MP-11) |
| `build_retriever` / `select_collections` | `src/medasist/retrieval/retriever.py` | Reusados para doc_types e para o funil por sub (MP-02) |
| `get_profile_config` / `PromptRegistry` | `src/medasist/profiles/schemas.py`, `generation/prompts.py` | Reusados na geração de cada sub |
| `_run_single` (corpo atual de `run_query`) | `src/medasist/generation/chain.py` | Extraído verbatim — garante identidade flag off |
| `_resolve_eval_models` validator | `src/medasist/config.py` | Estendido para resolver `retrieval_decompose_model=""` |
| `CitationResponse.from_item` | `src/medasist/api/schemas.py` | Reusado na serialização do campo aditivo (via `GenerationResult`) |

### Integration Points

| System | Integration Method |
|--------|--------------------|
| LM Studio (LLM de split) | `ChatOpenAI(...)` lazy com `_DECOMPOSE_PROMPT | llm | StrOutputParser` — retorna texto com uma sub-pergunta por linha |
| LM Studio (LLM de geração) | Reusado por `_run_single`/`stream_answer` por sub (nada muda) |
| ChromaDB | `retrieve(sub, subset, settings)` por sub — nada muda no armazenamento |
| API | `QueryResponse` ganha campo aditivo; `/query` e `/query/stream` byte-identical quando flag off |

---

## Components

### `src/medasist/retrieval/decompose.py` (new)

- **Purpose**: Decidir (deterministicamente) se uma pergunta é composta e, se sim, dividi-la em sub-perguntas via LLM local, com cap e degradação graciosa.
- **Location**: `src/medasist/retrieval/decompose.py`
- **Interfaces**:
  - `decompose_query(query: str, settings: Settings) -> list[str]` — público. Flag off → `[query]`; não-composta → `[query]` (sem LLM); composta → split + cap; falha/malformado/0/1 → `[query]`.
  - `_is_compound(query: str, settings: Settings) -> bool` — heurística determinística (Q4): gate em tokens TOTAIS (`retrieval_decompose_min_tokens`) + conectores `e`/`ou`/`e/ou` no texto bruto (pré-stopwords) ou vírgula seguida de tokens de conteúdo.
  - `_split(query: str, settings: Settings) -> list[str]` — privado; `ChatOpenAI` lazy, parse linha a linha, strip/filtra vazias.
- **Dependencies**: `Settings`, `_TOKEN_RE` local, `_DECOMPOSE_PROMPT` (PromptTemplate), `StrOutputParser`, `ChatOpenAI` lazy.
- **Reuses**: padrão de `query_rewrite.py` (lazy `ChatOpenAI`, `_TOKEN_RE` local, prompt module-level, `logger.exception`).
- **Nota**: NÃO importa de `retriever` nem `chain` (evita import circular).

### `src/medasist/generation/chain.py` (modify)

- **Purpose**: Orquestrar decomposição/merge no fluxo síncrono e de streaming, com identidade quando flag off.
- **Location**: `src/medasist/generation/chain.py`
- **Interfaces**:
  - `_run_single(question, stores, profile, settings=None, doc_types=None) -> GenerationResult` — corpo atual de `run_query` extraído verbatim (retrieval + cold start + citações + geração + validação).
  - `_merge_sub_results(question, sub_results, settings) -> GenerationResult` — re-numera citações 1-based, remapeia `[N]` (via `remap_answer`), concatena respostas, preenche `unanswered_sub_questions`; nenhuma citação válida → cold start total.
  - `run_query(...)` — `subs = decompose_query(question, settings)`; `len==1` → `_run_single`; `len>1` → `[ _run_single(s) for s in subs ]` + `_merge_sub_results`. `GenerationResult` ganha `unanswered_sub_questions: list[str] = field(default_factory=list)`.
  - `stream_answer(...)` — paridade: `subs = decompose_query(...)`; `len==1` → caminho atual; `len>1` → para cada sub gera deltas (via helper por sub) acumulando a resposta, e ao final `_merge_sub_results`; retorna `(merged_citations, is_cold_start)`.
- **Dependencies**: `decompose_query`, `build_retriever`, `select_collections`, `build_citations`, `validate_citations`, `remap_answer`, `get_profile_config`, `PromptRegistry`, `ChatOpenAI`.
- **Reuses**: `remap_answer` (citations.py), `decompose_query` (retrieval/decompose.py), estrutura existente de `run_query`/`stream_answer`.

### `src/medasist/generation/citations.py` (modify)

- **Purpose**: Remapear marcadores `[N]` de uma resposta por um offset (usado no merge de sub-respostas).
- **Location**: `src/medasist/generation/citations.py`
- **Interface**: `remap_answer(answer: str, offset: int) -> str` — substitui cada `[N]` por `[N+offset]`.
- **Dependencies**: `re`.
- **Reuses**: padrão regex `\[(\d+)\]` já usado em `validate_citations`.

### `src/medasist/api/schemas.py` (modify)

- **Purpose**: Expor o campo aditivo de sub-perguntas não respondidas no contrato flat.
- **Location**: `src/medasist/api/schemas.py`
- **Interface**: `QueryResponse.unanswered_sub_questions: list[str] = Field(default_factory=list)`; `from_result` mapeia de `GenerationResult.unanswered_sub_questions`.
- **Dependencies**: `GenerationResult`.
- **Reuses**: padrão `Field(default_factory=list)` já usado em `QueryResponse.citations`.

### `src/medasist/config.py` (modify)

- **Purpose**: Configurar a decomposição (flag, cap, modelo, temperatura, max_tokens, gate).
- **Location**: `src/medasist/config.py` (bloco Retrieval, ao lado de `retrieval_query_rewrite_*`)
- **Interfaces**: campos `retrieval_decompose_enabled/max_sub_questions/model/temperature/max_tokens/min_tokens`; `_resolve_eval_models` resolve `retrieval_decompose_model=""` → `lm_studio_llm_model`.
- **Reuses**: padrão de constraints e de resolução de modelo de `retrieval_query_rewrite_*`.

---

## Data Models

### Settings (config.py) — novas

```python
# Decomposição de perguntas multi-parte (RAG-03)
retrieval_decompose_enabled: bool = Field(default=False)
retrieval_decompose_max_sub_questions: int = Field(default=5, gt=0)
retrieval_decompose_model: str = Field(default="")          # vazio → lm_studio_llm_model
retrieval_decompose_temperature: float = Field(default=0.0, ge=0.0, le=2.0)
retrieval_decompose_max_tokens: int = Field(default=256, gt=0)
retrieval_decompose_min_tokens: int = Field(default=4, gt=0)   # gate: mínimo de TOKENS TOTAIS (_is_compound, Q4)
```

### GenerationResult (chain.py) — campo aditivo

```python
@dataclass(frozen=True)
class GenerationResult:
    answer: str
    citations: list[CitationItem] = field(default_factory=list)
    profile: UserProfile = UserProfile.MEDICO
    disclaimer: str = ""
    is_cold_start: bool = False
    unanswered_sub_questions: list[str] = field(default_factory=list)  # aditivo
```

### QueryResponse (schemas.py) — campo aditivo

```python
class QueryResponse(BaseModel):
    answer: str
    citations: list[CitationResponse]
    profile: UserProfile
    disclaimer: str
    is_cold_start: bool
    unanswered_sub_questions: list[str] = Field(default_factory=list)  # aditivo, retrocompatível
```

**Relationships**: nenhuma migração de banco; campos aditivos apenas.

---

## Error Handling Strategy

| Error Scenario | Handling | User Impact |
|----------------|----------|-------------|
| Split LLM falha/timeout | `_split` lança → `decompose_query` captura, `logger.exception`, retorna `[question]` | Identidade — pergunta tratada como única (MP-07) |
| Split retorna malformado/vazio/0 sub-perguntas | parse devolve lista vazia → `[question]` | Identidade (MP-07) |
| Split retorna 1 sub-pergunta | `len==1` → `[question]` (identidade) | Sem re-numeração indevida (MP-08) |
| Split retorna >cap | trunca nas `max_sub_questions` primeiras | Processa só o cap (MP-05) |
| Todas as subs miss | `_merge_sub_results` → cold start total | `cold_start_message`, zero geração (MP-09) |
| Algumas subs miss | hits no merged + misses em `unanswered_sub_questions` | Resposta parcial honesta, sem fabricar (MP-10) |
| Sub sem citação válida | tratada como miss, órfãos removidos antes da re-numeração | Não entra no merged (MP-11) |
| `stores` vazio | cold start antes do split (retrieve/`_run_single` decide) | LLM de split nunca chamado |

---

## Tech Decisions (only non-obvious ones)

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Q1 Campo aditivo | `unanswered_sub_questions: list[str]` (default `[]`) em `GenerationResult` e `QueryResponse` | Contrato FLAT preservado; retrocompatível (decisão 2) |
| Q2 Cap/custo | `max_sub_questions=5` + gate `_is_compound` (Q4) | Limita N×custo e evita split em não-compostas |
| Q3 Logging | Composto (nº subs/hits/misses) + por sub (logs existentes do `retrieve`) | Observabilidade sem quebrar testes de log (aditivo) |
| Q4 Critério composta | `_is_compound`: ≥`retrieval_decompose_min_tokens` (4) TOKENS TOTAIS (sem remoção de stopwords) E (conector `e`/`ou`/`e/ou` no texto bruto OU vírgula seguida de tokens de conteúdo); só aciona split se também retornar >1 sub | Determinístico e com custo limitado; conectores detectados no bruto pois `e` é stopword; gate em tokens totais distingue "…dipirona e …álcool?" (10 tok, composta) de "Alphazol ou Betazol" (3 tok, não-composta) |
| Split layer | `retrieval/decompose.py` (não em `retriever.py`) | Orquestração acima do funil; cada sub chama `retrieve()`; evita import circular |
| Merge reuso | `_merge_sub_results` + `remap_answer` compartilhados por `run_query` e `stream_answer` | Paridade P2 (MP-14); DRY |
| Identidade flag off | `_run_single` = corpo atual de `run_query` extraído verbatim | Byte-identical garantido (MP-01) |
| Streaming schema | `unanswered_sub_questions` só no `QueryResponse` flat; SSE inalterado | P2 exige mesmo caminho, não mesmo payload; escopo contido |

---

## Mitigations for CONCERNS.md Items

- **H4 (sync-in-async bloqueia event loop):** nenhuma mudança de async; `run_query`/`stream_answer` continuam síncronos e as rotas `def` rodam no threadpool (L-004). O split é síncrono e roda no mesmo thread.
- **H6 (doc_types silenciosamente ignorado — já resolvido por AD-004):** cada sub-pergunta respeita `select_collections(stores, doc_types)` via `_run_single`/`retrieve` (MP-02).
- **M7 (broad `except Exception` no retriever):** o split usa `except Exception` + `logger.exception` de propósito (degradação graciosa do LLM não-confiável), coerente com `query_rewrite.py`; não altera o retriever.
- **L4 (settings singleton nunca resetado):** testes do novo setting usam `Settings` instanciada com env (fixture), não `get_settings()` mutado.
- **L1 (coverage gap — `api/schemas.py`):** a serialização do campo aditivo é testada via `TestClient`/`QueryResponse` em `test_query.py` (MP-10), reduzindo lacuna conhecida.
- **L6 (sem retry em LM Studio):** o split herda `llm_max_retries`/`llm_request_timeout` do `ChatOpenAI`; falha após retries → identidade (MP-07), nunca propaga.
- **L8 (sem sanitização de prompt injection):** sub-perguntas são texto do split LLM e entram como `question` do prompt de geração de cada sub. **Mitigação:** sub-perguntas usadas apenas como a pergunta daquele sub (necessário), nunca a lista completa/original composta além disso (MP-13); comprimento limitado por `max_tokens`/cap e parse robusto; falha → identidade.

---

## Open Questions

None — Q1–Q4 resolvidas e registradas em `spec.md`.
