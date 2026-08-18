# RAG-01: Reranking Cross-Encoder — Technical Spec

**Path:** `.specs/features/rag-01-reranking/spec.md`
**TLC scope:** large (spec.md + design.md + tasks.md)
**Based on story:** Reordenar por relevância os trechos recuperados (top-N) com um reranker cross-encoder antes da geração, para fundamentar respostas nos trechos mais relevantes e elevar MRR / Context Precision.
**Status:** Awaiting human approval

---

## Problem Statement

O MedAssist ordena os trechos recuperados apenas pela distância L2 do embedding (ANN), que reflete similaridade semântica aproximada do query, não a relevância fina do trecho para a pergunta. Isso pode posicionar trechos pouco úteis no topo do contexto e reduzir a qualidade da recuperação (MRR, Context Precision). Este feature insere um **reranker cross-encoder** (sentence-transformers) no funil único `retrieve()`, reordenando os candidatos já filtrados pelo threshold L2 antes da geração — sem mudar a assinatura pública nem o contrato da chain.

## Goals

- [ ] Recuperação retornada por `retrieve()` ordenada por score do reranker (maior primeiro) quando habilitado, respeitando `retrieval_top_k`.
- [ ] Qualidade de recuperação medida: MRR customizado e Context Precision da avaliação RAGAS sobre o subconjunto não-cold-start.
- [ ] Degradação graciosa: falha/ausência do reranker nunca falha a query (ordem L2 preservada, erro em log).

## Out of Scope

| Feature | Reason |
|---------|--------|
| Busca híbrida (denso + esparso) | História separada RAG-02. |
| Substituir a arquitetura de retrieval | `retrieve()` continua sendo o funil único; assinatura pública `list[Document]` inalterada. |
| Novos endpoints de API / alterar contrato `POST /query` | Sem mudança de superfície HTTP. |
| Cache de respostas ou de resultados do reranker | Fora do escopo; latência contida por batch + `rerank_top_n`. |
| Alterar threshold L2 (`retrieval_score_threshold`) | Cold start permanece decidido no L2 pré-rerank. |
| Alterar templates de prompt | Sem mudança em `generation/prompts.py`. |

---

## User Stories

### P1: Reranking cross-encoder no funil único de retrieval ⭐ MVP

**User Story**: Como profissional de saúde que consulta o MedAssist, quero que os trechos recuperados sejam reordenados por relevância (reranker cross-encoder) antes da geração, para que a resposta seja fundamentada nos trechos mais relevantes e a recuperação tenha maior qualidade (MRR, Context Precision).

**Why P1**: É o núcleo do feature — melhora a qualidade da fundamentação das respostas mantendo as regras de segurança (cold start no L2 pré-rerank, contrato público intacto) e degradação graciosa.

**Acceptance Criteria**:

1. WHEN o reranker está habilitado e o modelo carregado, e `retrieve()` encontra candidatos acima do threshold L2, THEN os `Document` retornados SHALL vir ordenados por score do reranker (maior primeiro) e a lista final SHALL respeitar `retrieval_top_k`.
2. WHEN o reranker está habilitado e os candidatos vêm de múltiplas coleções (até 4 stores), THEN todos os candidatos até o limite `rerank_top_n` SHALL ser pontuados em uma única chamada em batch ao reranker.
3. WHEN o fluxo de avaliação RAGAS chama `retrieve()` diretamente em `_collect_rows`, THEN o rerank SHALL ser aplicado no mesmo caminho de produção, mantendo os contexts da avaliação idênticos aos usados na resposta (invariante AD-011).
4. WHEN um contexto rerankado é usado pela chain para gerar a resposta, THEN todas as citações `[N]` SHALL continuar correspondendo a `CitationItem` válidos.
5. WHEN o reranker está habilitado mas a chamada a ele falha (erro, timeout ou modelo ausente), THEN a query SHALL não falhar: o erro SHALL ser registrado em log e os documentos SHALL retornar na ordem original por distância L2.
6. WHEN o reranker está habilitado e nenhum candidato supera o threshold L2 (cold start), THEN o comportamento SHALL permanecer inalterado — retorno de lista vazia e mensagem fixa — e o reranker SHALL não ser chamado.
7. WHEN o reranker está desabilitado na configuração, THEN `retrieve()` SHALL retornar os documentos na ordem L2 original e o reranker SHALL não ser carregado nem chamado (identidade).
8. WHEN o rerank é aplicado, THEN a decisão de cold start SHALL continuar baseada no threshold L2 pré-rerank: o rerank SHALL nunca transformar um retrieval não-cold-start em cold start (regra de segurança médica).
9. WHEN `retrieve()` retorna sob qualquer configuração de rerank, THEN a assinatura pública SHALL permanecer `list[Document]` e a chain (`run_query`) SHALL não mudar de contrato.
10. WHEN o corte final é aplicado com rerank habilitado, THEN o número de documentos retornados SHALL ser no máximo `retrieval_top_k`.
11. WHEN novas configurações `retrieval_rerank_*` são carregadas via pydantic-settings, THEN todas SHALL ter restrições de valor (ex.: `gt=0`) e entradas correspondentes em `.env.example`.
12. WHEN múltiplas queries concorrem com o modelo do reranker compartilhado, THEN o modelo SHALL ser carregado uma única vez (singleton thread-safe) e reutilizado, sem recarregamento por query.
13. WHEN o rerank é aplicado, THEN ele SHALL acontecer DEPOIS do guarda lexical `_lexical_relevance_guard` (o guarda é binário e barato; o reranker nunca vê candidatos guarda-filtered).
14. WHEN o rerank é habilitado sem valor explícito de `rerank_top_n`, THEN o default SHALL ser `20` (>= `retrieval_top_k`), limitando o batch enviado ao reranker.

**Independent Test**: `pytest tests/retrieval/test_retriever.py -v` — com `retrieval_rerank_enabled=True` e `CrossEncoder` mockado retornando scores decrescentes que invertem a ordem L2, `retrieve()` retorna os documentos na ordem do mock, respeitando `retrieval_top_k`; com o mock lançando exceção, retorna na ordem L2 e não falha; com flag off, ordem L2 intacta.

---

### P2: Métrica customizada MRR na avaliação RAGAS

**User Story**: Como mantenedor do MedAssist, quero medir o MRR (rank do primeiro hit em `reference_contexts`) sobre os `contexts` retornados por `retrieve()`, agregado sobre o subconjunto não-cold-start, para quantificar o ganho do rerank na posição dos trechos relevantes.

**Why P2**: Métrica de avaliação (ferramenta de desenvolvimento), não funcionalidade de usuário final; essencial para validar o ganho de qualidade do P1.

**Acceptance Criteria**:

1. WHEN a avaliação RAGAS roda, THEN o MRR customizado SHALL ser calculado por pergunta como o inverso do rank do primeiro hit de `GoldenQuestion.reference_contexts` sobre `contexts` retornados por `retrieve()`.
2. WHEN a agregação é montada, THEN o MRR SHALL ser agregado (média) sobre o subconjunto não-cold-start e exposto em `aggregates` do `EvaluationReport` (ex.: chave `mrr`).

**Independent Test**: `pytest tests/evaluation/test_metrics.py -v` — com `contexts` mockados e `reference_contexts` conhecidos, `_reciprocal_rank` retorna o valor esperado; agregado calcula a média correta e exclui cold starts.

---

## Edge Cases

- WHEN o reranker falha ou o modelo está indisponível, THEN degradação graciosa para a ordem L2 original, com erro registrado em log; a query nunca falha.
- WHEN nenhum candidato supera o threshold L2, THEN cold start inalterado; o reranker não é chamado.
- WHEN o rerank está desabilitado, THEN identidade (ordem L2 preservada); o modelo não é carregado.
- WHEN há candidatos de múltiplas coleções (até 4 stores × `retrieval_top_k` = até 40), THEN pontuação em batch único; `rerank_top_n` pode limitar o subconjunto enviado ao reranker.
- WHEN há empates de score do reranker entre candidatos, THEN a ordenação resultante SHALL ser determinística (tie-break pela ordem L2 original) e coberta por teste.
- WHEN o ambiente não tem o modelo local (ex.: CI sem download offline), THEN comportamento de identidade (ordem L2), sem falha.
- WHEN a hot path é síncrona, THEN a chamada do reranker em batch único e o limite `rerank_top_n` contêm o custo de latência.
- WHEN o cold start é decidido pré-rerank, THEN o rerank não esvazia um contexto que já era válido pela distância L2.
- WHEN o rerank é habilitado mas a lista após o guarda lexical é vazia (cold start lexical), THEN o reranker não é chamado e `retrieve()` retorna lista vazia (comportamento inalterado).

---

## Requirement Traceability

| Requirement ID | Story | Phase | Status |
|----------------|-------|-------|--------|
| RAG01-01 | P1 (AC1) | Design | Pending |
| RAG01-02 | P1 (AC2) | Design | Pending |
| RAG01-03 | P1 (AC3) | Design | Pending |
| RAG01-04 | P1 (AC4) | Design | Pending |
| RAG01-05 | P1 (AC5) | Design | Pending |
| RAG01-06 | P1 (AC6) | Design | Pending |
| RAG01-07 | P1 (AC7) | Design | Pending |
| RAG01-08 | P1 (AC8) | Design | Pending |
| RAG01-09 | P1 (AC9) | Design | Pending |
| RAG01-10 | P1 (AC10) | Design | Pending |
| RAG01-11 | P1 (AC11) | Design | Pending |
| RAG01-12 | P1 (AC12) | Design | Pending |
| RAG01-13 | P1 (AC13) | Design | Pending |
| RAG01-14 | P1 (AC14) | Design | Pending |
| RAG01-15 | P2 (AC1, AC2) | Design | Pending |

**Coverage:** 15 total, 0 mapped to tasks (pending tasks.md), 15 unmapped ⚠️

---

## Decisiones Resolvidas (Q1–Q8 — vinculantes)

| # | Pergunta | Decisão (vinculante) |
|---|----------|----------------------|
| Q1 | Runtime do reranker | **sentence-transformers** cross-encoder real (default `BAAI/bge-reranker-base`). NÃO é LM Studio (que não expõe endpoint de rerank). |
| Q2 | Aquisição do modelo | Download via HuggingFace hub para cache local no primeiro uso; carregado **uma vez** (lazy singleton). Testes mockam a classe `CrossEncoder` — nunca dependem de rede. |
| Q3 | Default da flag | `retrieval_rerank_enabled` **default off**. Comportamento atual preservado até opt-in; CI/testes sem dependência do modelo. |
| Q4 | Semântica do score | O score do reranker **substitui** a ordenação L2 (candidatos já filtrados pelo threshold L2 são reordenados por score do reranker, maior primeiro). Cold start decidido **PRÉ-rerank** no L2 — rerank nunca esvazia contexto. **NÃO** há gate de cold start por score do reranker. |
| Q5 | `rerank_top_n` default | **20** (>= `retrieval_top_k`=10). Justificativa: cobre o conjunto típico de candidatos (até 4 stores) com margem, limitando o custo da hot path. Restrição `gt=0`. |
| Q6 | Ordem rerank vs guarda lexical | **Rerank DEPOIS do guarda lexical.** O guarda é binário e barato; o reranker nunca vê candidatos guarda-filtered. O cold start lexical (lista vazia) preserva o comportamento atual e evita desperdício de chamada ao modelo. |
| Q7 | Métrica de sucesso | Manter RAGAS `context_precision`/`context_recall` E **adicionar MRR customizado** no eval (rank do primeiro hit em `GoldenQuestion.reference_contexts` sobre `contexts` de `retrieve()`), agregado sobre o subconjunto não-cold-start. |
| Q8 | Lifecycle do modelo na API | **Lazy singleton thread-safe** (double-checked locking, mesmo padrão de `store.get_client`): modelo carregado na primeira chamada a `retrieve()`, não no lifespan. Evita carga pesada no startup e não regride os testes de API (que mockam `build_chain`); quando habilitado, o CrossEncoder é mockado nos testes. |

---

## Data Model Changes

- **Config (`src/medasist/config.py`)** — 4 novas settings no bloco `# Retrieval`:
  - `retrieval_rerank_enabled: bool = Field(default=False)`
  - `retrieval_rerank_model: str = Field(default="BAAI/bge-reranker-base")`
  - `retrieval_rerank_top_n: int = Field(default=20, gt=0)`
  - `retrieval_rerank_batch_size: int = Field(default=16, gt=0)` *(opcional de controle do batch do CrossEncoder.predict; default 16)*
- **`.env.example`** — entradas documentadas para as 4 settings acima.
- **ChromaDB / schema de documentos / golden set:** nenhuma mudança. Rerank é read-only sobre os `Document` recuperados.

---

## Process / Background Flow

**Happy path (rerank habilitado):**
1. `retrieve()` coleta candidatos por store via `similarity_search_with_score` e filtra pelo threshold L2.
2. Se nenhum candidato → cold start (retorna `[]`, reranker não chamado).
3. Dedup por `page_content`, ordena por distância L2.
4. `_lexical_relevance_guard` (barato, binário) — pode esvaziar (cold start lexical).
5. Se habilitado e `guarded` não-vazio: `rerank_documents(guarded, query, settings)` reordena por score do reranker (desc), corte em `retrieval_top_k`.
6. Se desabilitado: `guarded[:k]` (ordem L2 original).
7. Retorna `list[Document]` (máx. `retrieval_top_k`).

**Failure path — reranker falha/timeout/modelo ausente:**
- `rerank_documents` captura a exceção, registra `logger.exception`/`logger.warning` e retorna os documentos **na ordem L2 original**. `retrieve()` nunca propaga a falha; a query continua com a ordem L2 (identidade degradada).

**Failure path — cold start (pré-rerank):**
- `candidates` vazio após o filtro L2 → `retrieve()` retorna `[]` antes de qualquer chamada ao reranker. A decisão de cold start é sempre pré-rerank; o rerank jamais esvazia um contexto já válido.

---

## API Changes

No API changes. `POST /query` e `GET /health` mantêm contrato; nenhum endpoint novo. `run_query`/`build_chain` não mudam de assinatura.

---

## Frontend Changes

No frontend changes. A UI Streamlit e `ui/client.py` são intocados (o rerank é interno ao backend).

---

## Tests Required

**Unit / Integration:**
- `tests/retrieval/test_reranker.py` (novo): singleton lazy (carregado uma vez), `rerank_documents` reordena por score desc, degradação em falha (ordem L2), batch único sobre `rerank_top_n`, tie-break determinístico, não chamado quando desabilitado, não chamado em cold start.
- `tests/retrieval/test_retriever.py` (atualizar): `retrieve()` integrado — rerank habilitado reordena e respeita top_k; desabilitado = identidade; falha = ordem L2 sem erro; cold start não chama reranker; contrato `list[Document]` inalterado.
- `tests/config/test_config.py` (novo/atualizar): defaults das 4 settings, restrições `gt=0` (rejeita 0/negativo), valores via env.
- `tests/generation/test_chain.py` (atualizar se necessário): contrato `run_query` inalterado; citações `[N]` seguem válidas com contexto rerankado (RAG01-04).
- `tests/evaluation/test_metrics.py` (atualizar): `_reciprocal_rank`, agregação MRR sobre subconjunto não-cold-start, exposição em `aggregates` (RAG01-15).
- `tests/scripts/test_evaluate_rag.py` (atualizar): relatório expõe MRR quando presente.

**Existing tests that will break:**
- Nenhum esperado se flag off for default (comportamento de identidade). Os testes existentes de `test_retriever.py` devem continuar passando com a flag off.

---

## Files That Will Change

| File | Change type | Why |
|------|-------------|-----|
| `src/medasist/config.py` | Modify | Novas settings `retrieval_rerank_*` (RAG01-11). |
| `.env.example` | Modify | Documentar as novas settings (RAG01-11). |
| `src/medasist/retrieval/reranker.py` | New | Helper `rerank_documents` + singleton lazy thread-safe do CrossEncoder (RAG01-01/02/05/07/12). |
| `src/medasist/retrieval/retriever.py` | Modify | Plug do rerank em `retrieve()` após `_lexical_relevance_guard` (RAG01-01/06/08/10/13). |
| `src/medasist/evaluation/metrics.py` | Modify | MRR customizado + agregação (RAG01-15). |
| `requirements.txt` (+ `requirements-dev.txt` se aplicável) | Modify | Adicionar `sentence-transformers` (nova dependência). |
| `tests/retrieval/test_reranker.py` | New | Testes do helper/singleton. |
| `tests/retrieval/test_retriever.py` | Modify | Testes integrados do rerank em `retrieve()`. |
| `tests/config/test_config.py` | New/Modify | Testes das novas settings. |
| `tests/evaluation/test_metrics.py` | Modify | Testes do MRR. |
| `tests/scripts/test_evaluate_rag.py` | Modify | Relatório com MRR (se aplicável). |

---

## Risks

| # | Risco | Mitigação |
|---|-------|-----------|
| R1 | Nova dependência `sentence-transformers` + download de modelo | Degrada sem rede (identidade/ordem L2); flag off default; testes mockam `CrossEncoder`. |
| R2 | Latência na hot path síncrona | Batch único + limite `rerank_top_n`; modelo lazy singleton sem recarga por query. |
| R3 | Cold start decidido pré-rerank | Rerank atua apenas sobre candidatos já aprovados no L2; nunca esvazia contexto. |
| R4 | Rerank fora do funil `retrieve()` (só na chain) quebraria o invariante AD-011 do eval | Rerank vive dentro de `retrieve()` (helper compartilhado); `_collect_rows` e `run_query` usam o mesmo caminho; tipo de retorno `list[Document]` inalterado. |
| R5 | Settings novas sem constraints / sem `.env.example` | `gt=0` nos campos numéricos; entradas documentadas (RAG01-11). |
| R6 | Modelo compartilhado recarregado por query / races | Singleton thread-safe com double-checked locking (padrão `get_client`). |
| R7 | Falha do reranker derruba a query | `try/except` + log; retorno na ordem L2 (degradação graciosa). |
| R8 | Tests de API disparam carga pesada de modelo no startup | Lazy-load (não no lifespan); flag off default; testes mockam `CrossEncoder`. |
| R9 | Empates de score do reranker causam ordem não-determinística | Tie-break pela ordem L2 original (índice), estável e testado. |
| R10 | CONCERNS M7 — `except Exception` amplo no retriever engole erros de programação | Reaproveitar o padrão existente, mas limitar o catch ao escopo do rerank (isolado no helper) e usar `logger.exception`; programar o helper para re-levantar apenas erros não-relacionados ao reranker, se aplicável. |

---

## Open Questions

None. (Q1–Q8 resolvidas e vinculantes; registradas acima.)

---

## Success Criteria

- [ ] `pytest tests/ -v --cov=src --cov-fail-under=80` passa com flag off default (identidade, sem regressão).
- [ ] Com flag on e `CrossEncoder` mockado, `retrieve()` retorna docs ordenados por score do reranker, máx. `retrieval_top_k`, contrato `list[Document]` intacto.
- [ ] Falha do reranker (mock lançando exceção) não falha a query — ordem L2 retornada e erro logado.
- [ ] Eval RAGAS computa e expõe MRR (agregado sobre não-cold-start), além de `context_precision`/`context_recall`.
- [ ] MRR/Context Precision registram ganho (ou não-regressão) ao comparar rerank on vs off no golden set.
