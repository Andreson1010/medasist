# Spec — OBS-03: Avaliação RAG (RAGAS)

**Slug:** `obs-03-rag-evaluation`
**Escopo TLC:** large (spec.md + design.md + tasks.md)
**Story aprovada:** Checkpoint 1 (2026-08-11).
**Decisões das perguntas em aberto (aprovadas):**
1. **Scores descartados no retriever — NÃO alterar `retriever.retrieve()`.** O eval usa `retrieve()` como está (já retorna docs ordenados por distância L2 e filtrados pelo threshold — suficiente para os `contexts` de RAGAS). RAGAS 0.2.15, para as 4 métricas em questão, consome `question`, `contexts`, `answer` e `reference` (ground truth); os scores L2 não são consumidos diretamente. Nenhuma mudança em `src/medasist/retrieval/retriever.py`.
2. **Judge = mesmo modelo que gera.** Aceito como limitação inicial (o próprio LLM avalia respostas que gerou). Nova setting `eval_llm_model` com default = `lm_studio_llm_model`, permitindo apontar para modelo diferente no futuro. Documentado como limitação conhecida em "Riscos".
3. **`reference_contexts` OPCIONAL.** O golden set define `reference_answer` (ground truth) — obrigatório para ContextRecall/ContextPrecision. `reference_contexts` é opcional; se ausente, RAGAS usa `reference_answer` para ambas as métricas. Os `contexts` (recuperados) sempre vêm do `retrieve()` real do pipeline — nunca do golden set.
4. **Metadados sem section/page — confirma-se.** Apenas `source_path` é usado nos metadados; o golden set NÃO deve exigir seção/página (o schema não inclui esses campos).

---

## Contexto

`src/medasist/evaluation/__init__.py` é um **stub vazio**. `scripts/evaluate_rag.py` **não existe** (referenciado em CLAUDE.md/README.md antigos e removido de docs por inexistir — ver `.specs/project/STATE.md`). `evals/` não existe e **não está no `.gitignore`** → será versionado. `ragas==0.2.15` e `datasets==3.6.0` estão pinados em `requirements.txt` e instalados, mas não há código consumindo-os.

O pipeline de query é: `POST /query` → `run_query(question, stores, profile, settings, doc_types)` (`src/medasist/generation/chain.py:70`) → `retriever.invoke()` (cold start guard) → `ChatOpenAI(LM Studio)` → `validate_citations` → `GenerationResult(answer, citations, profile, disclaimer, is_cold_start)`. `build_chain(stores, profile, settings)` (`chain.py:181`) faz curry de `run_query`. `retrieve(query, stores, settings)` (`src/medasist/retrieval/retriever.py:84`) retorna `list[Document]` ordenado por distância L2 com threshold `retrieval_score_threshold=0.4` e dedup por `page_content`. Metadados dos chunks: `doc_type`, `source_path`, `sha256`, `chunk_index`, `char_count` — **sem section/page**.

Infra disponível: `get_client`, `build_embeddings`, `get_vectorstore`, `get_all_vectorstores` em `src/medasist/vectorstore/store.py`; `check_lm_studio` (probe `GET {base_url}/models` via httpx) em `src/medasist/api/health.py:124` como referência de probe offline.

Não existe nenhum mecanismo de avaliação de qualidade do RAG — alterações no retriever, prompts ou dados não são verificadas contra regressões. Este feature fecha essa lacuna com avaliação **offline** (sem API HTTP, sem nuvem).

## Objetivo

Permitir rodar `python scripts/evaluate_rag.py --dataset evals/dataset/golden_set.json` para avaliar retrieval + geração sobre um **golden set sintético** com as métricas **Context Precision**, **Context Recall**, **Faithfulness** e **Answer Relevancy**, com LLM e embeddings locais (LM Studio), fail-fast quando o LM Studio está fora, cold starts sinalizados e excluídos das métricas de geração, e código de saída semântico (0 sucesso / != 0 falha) — sem passar pela API HTTP e sem depender de serviços em nuvem.

## Arquitetura da solução

Novo pacote `src/medasist/evaluation/` (módulos `dataset.py`, `metrics.py`, `__init__.py`) + script `scripts/evaluate_rag.py` + golden set versionado `evals/dataset/golden_set.json`. O eval resolve cada pergunta **diretamente pelo pipeline** (`retrieve` para `contexts`, `run_query` para `answer`), monta um `datasets.Dataset`, e chama `ragas.evaluate` com as 4 métricas usando wrappers LangChain apontando para o LM Studio. Sem alterações em `retriever.py`, `chain.py`, nem na API.

### Fluxo de dados (alto nível)

```
golden_set.json ──load_golden_set──▶ GoldenSet (Pydantic) ──▶ datasets.Dataset
                                                                      │
script: probe LM Studio (fail fast) ──▶ probe coleções (fail fast)    │
                                                                      ▼
              evaluate_golden_set: por pergunta ─▶ retrieve() ─▶ contexts
                                                └▶ run_query() ─▶ answer + is_cold_start
                                                                      │
                                              cold start? ──▶ exclui de Faithfulness/AnswerRelevancy
                                                                      ▼
                                            ragas.evaluate(llm=LM Studio, embeddings=LM Studio)
                                                                      │
                                                                      ▼
                              EvaluationResult ──▶ agregadas + por pergunta + (opcional) JSON report
```

## Arquivos que serão alterados

| Arquivo | Mudança |
|---|---|
| `src/medasist/config.py` | Novas settings `eval_golden_set_path` (Path, default `evals/dataset/golden_set.json`), `eval_llm_model` (str, default `lm_studio_llm_model`), `eval_embedding_model` (str, default `lm_studio_embedding_model`), `eval_batch_size` (int, `gt=0`, default `16`). |
| `src/medasist/evaluation/__init__.py` | Exports públicos (`GoldenQuestion`, `GoldenSet`, `load_golden_set`, `build_eval_llm`, `build_eval_embeddings`, `build_metrics`, `evaluate_golden_set`) + `__all__`. |
| `src/medasist/evaluation/dataset.py` | **Novo** — modelos Pydantic `GoldenQuestion`/`GoldenSet` e `load_golden_set(path)` com validação e conversão para `datasets.Dataset`. |
| `src/medasist/evaluation/metrics.py` | **Novo** — `build_eval_llm`, `build_eval_embeddings`, `build_metrics`, `evaluate_golden_set`. |
| `scripts/evaluate_rag.py` | **Novo** — CLI com argparse, probes fail-fast, execução da avaliação e relatório. |
| `evals/dataset/golden_set.json` | **Novo** — golden set versionado (~8-10 perguntas sintéticas PT-BR sobre medicamentos fictícios). |
| `.env.example` | Documentar `EVAL_GOLDEN_SET_PATH`, `EVAL_LLM_MODEL`, `EVAL_EMBEDDING_MODEL`, `EVAL_BATCH_SIZE`. |
| `.gitignore` | Adicionar `evals/results/` (relatórios de saída não versionados). |
| `CLAUDE.md` | Linha "Avaliação RAG" em Comandos Comuns (`python scripts/evaluate_rag.py ...`). |
| `README.md` | Menção à avaliação offline (seção Avaliação / Comandos). |

> Observação: `.env.example` está em `.gitignore`, mas é rastreado (`git ls-files` o lista) — a mudança será commitada normalmente.

## Mudanças de modelo de dados

- **Config:** 4 novas settings (ver tabela). Nenhuma alteração de schema existente.
- **Golden set (novo, versionado):** schema Pydantic definido em `dataset.py` (ver `design.md` — seção "Schema do golden set").
- **Relatório JSON de saída (novo, não versionado):** schema definido em `design.md` — seção "Schema do relatório JSON".
- **Banco de dados (ChromaDB):** **nenhuma mudança** — o eval é somente leitura.

## Detalhe por componente

### `src/medasist/evaluation/dataset.py`

- `GoldenQuestion(BaseModel)`: `question: str` (não vazio), `reference_answer: str` (não vazio), `reference_contexts: list[str] = []` (opcional), `doc_types: list[DocType] = []` (opcional, valida valores do enum), `profile: UserProfile = UserProfile.MEDICO`, `is_cold_start: bool = False` (flag explícita para perguntas que devem validar cold start).
- `GoldenSet(BaseModel)`: `version: str`, `description: str`, `questions: list[GoldenQuestion]`; validação custom: lista não vazia, `question`/`reference_answer` não vazios, `doc_types` com valores válidos.
- `load_golden_set(path) -> tuple[GoldenSet, datasets.Dataset]`: lê JSON (erro descritivo com nome do campo em `ValidationError` do Pydantic; `json.JSONDecodeError` → "arquivo malformado em <path>: <mensagem>"), valida, converte para `datasets.Dataset.from_list` com colunas `question`, `contexts` (vazias por ora — preenchidas na avaliação), `reference_answer`, `reference_contexts`, `is_cold_start`. Retorna também o `GoldenSet` validado.
- Erros: tipos de exceção customizados ou `ValueError` com caminho do campo — a mensagem deve citar o campo e o registro (índice) quando aplicável.

### `src/medasist/evaluation/metrics.py`

- `build_eval_llm(settings)`: `ChatOpenAI(base_url=lm_studio_base_url, api_key=lm_studio_api_key, model=eval_llm_model, temperature=0.0, max_tokens=...)` envolvido em `ragas.llms.LangchainLLMWrapper`. Temperatura 0.0 para determinismo do judge.
- `build_eval_embeddings(settings)`: `OpenAIEmbeddings(base_url=lm_studio_base_url, api_key=..., model=eval_embedding_model, check_embedding_ctx_length=False)` envolvido em `ragas.embeddings.LangchainEmbeddingsWrapper`.
- `build_metrics() -> list[Metric]`: `[ContextPrecision(), ContextRecall(), Faithfulness(), AnswerRelevancy()]`.
- `evaluate_golden_set(questions, stores, settings, profile, doc_types_override=None, batch_size=None) -> EvaluationResult`:
  - Por pergunta: `contexts = [d.page_content for d in retrieve(question, stores, settings)]`; `result = run_query(question, stores, profile, settings, doc_types)`; coluna `answer` = `result.answer`; coluna `is_cold_start` = `result.is_cold_start`; `contexts` da pergunta.
  - **Cold start:** perguntas com `is_cold_start=True` no resultado **não entram** no dataset passado a `ragas.evaluate` para Faithfulness/AnswerRelevancy; contagem reportada. Duas estratégias compatíveis (decidir na implementação, preferir a 2ª):
    1. Executar `ragas.evaluate` com todas e filtrar métricas de geração depois.
    2. Particionar: subconjunto de geração = apenas não-cold-start; subconjunto de retrieval = todas as perguntas (ContextPrecision/Recall valem mesmo com 0 contexts).
  - Agregadas: média por métrica + `num_questions`, `num_cold_start`, `num_generation_evaluated`.
- Sem `print()` no módulo de biblioteca — log via `logger`.

### `scripts/evaluate_rag.py`

- Segue a convenção de `scripts/ingest_docs.py`: `parse_args(argv)->Namespace`, `main(argv)->int`, `sys.exit(main())`, `logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")`, `from __future__ import annotations`, import via `pythonpath=["src","scripts"]`.
- Argparse: `--dataset` (Path, default `settings.eval_golden_set_path`), `--top-k` (int, override de `retrieval_top_k` via `settings.model_copy`), `--n` (int, limita nº de perguntas), `--profile` (choices dos UserProfile, default `medico`), `--doc-types` (nargs="+", choices dos DocType), `--output` (Path, opcional, grava relatório JSON).
- **Fail fast (ordem):** 1) dataset carregável/schema válido; 2) probe LM Studio via `GET {base_url}/models` com timeout `healthcheck_timeout` (espelha `api/health.py:check_lm_studio`; erro claro + `return 1`); 3) coleções: `get_all_vectorstores` + checagem de `collection.count() > 0` nas selecionadas (vazia/ausente → erro claro + `return 1`).
- **Saída (stdout):** agregadas por métrica (formatadas) + tabela por pergunta. Relatório JSON opcional em `--output`.
- **Código de saída:** `0` sucesso (mesmo com cold starts; métricas de geração vazias → `!= 0` se nenhuma pergunta válida para geração e geração solicitada); `1` para: dataset inválido/malformado/vazio, coleções vazias/ausentes, LM Studio fora, nenhuma pergunta válida, erro interno de RAGAS (traceback limpo).

### `evals/dataset/golden_set.json`

- ~8-10 perguntas sintéticas em PT-BR sobre medicamentos **fictícios** (Alphazol, Zolatril etc. — já usados nos testes) e diretrizes/protocolos fictícios. Nenhum dado real de paciente. Campos por pergunta: `question`, `reference_answer`, opcional `reference_contexts`, opcional `doc_types`, opcional `profile`, opcional `is_cold_start`. Pelo menos 1 pergunta com `is_cold_start=true` para validar CA-05.

## Requisitos (IDs traçam aos CAs da story)

| ID | AC | Requisito |
|---|---|---|
| REQ-1 | CA-01 | `load_golden_set(path)` carrega `evals/dataset/golden_set.json` com schema válido via `datasets.Dataset.from_list` sem erro; schema validado por modelos Pydantic com erro descritivo (campo + registro) para registros fora do padrão. |
| REQ-2 | CA-02 | `scripts/evaluate_rag.py` roda `ragas.evaluate` com as 4 métricas (ContextPrecision, ContextRecall, Faithfulness, AnswerRelevancy), imprime agregadas + por pergunta, e retorna/sai com código 0 com golden set não vazio e LM Studio disponível. |
| REQ-3 | CA-03 | LLM judge (`build_eval_llm`) e embeddings (`build_eval_embeddings`) apontam exclusivamente para `lm_studio_base_url`; nenhum endpoint externo é chamado (sem API da OpenAI, sem HuggingFace). |
| REQ-4 | CA-04 | LM Studio indisponível → probe fail-fast antes de qualquer avaliação: erro claro no stdout, código de saída != 0, sem timeout longo (limitado por `healthcheck_timeout`). |
| REQ-5 | CA-05 | Perguntas com resultado `is_cold_start=True` são sinalizadas no relatório e **excluídas** das métricas de geração (Faithfulness/AnswerRelevancy); contagem de cold starts reportada. |
| REQ-6 | CA-06 | Código de saída semântico: `0` em sucesso; `!= 0` em dataset inválido, coleções vazias/ausentes, LM Studio fora, zero perguntas válidas, erro interno de RAGAS. |
| REQ-7 | CA-07 | Perguntas resolvidas via `run_query` (resposta) e `retrieve` (contexts) direto — sem passar pela API HTTP; embeddings injetados via wrapper LangChain (`LangchainEmbeddingsWrapper`/`LangchainLLMWrapper`) sobre os mesmos vectorstores ChromaDB locais. |
| REQ-8 | Edge | Golden set vazio → erro claro + `!= 0`. |
| REQ-9 | Edge | JSON malformado → erro descritivo (path + mensagem) + `!= 0`. |
| REQ-10 | Edge | Schema inválido → `ValidationError` descritivo com campo + registro + `!= 0`. |
| REQ-11 | Edge | Coleções ChromaDB vazias/ausentes → reportado e `!= 0`. |
| REQ-12 | Edge | Nenhuma pergunta passa o threshold (`retrieve()` → 0 contexts em todas) → 0 válidas → `!= 0`. |
| REQ-13 | Edge | RAGAS incompatível/erro interno → traceback limpo (mensagem única) + `!= 0`. |
| REQ-14 | Segurança | Nenhum dado real de paciente; golden set 100% sintético; sem expor scores de retrieval; `%s` lazy no logging. |
| REQ-15 | Segurança | `reference_answer`/`reference_contexts` usados apenas como ground truth de avaliação; nunca injetados no pipeline de geração. |

## Riscos

| Risco | Nível | Mitigação |
|---|---|---|
| Judge = modelo que gera (viés de autogratificação) | Alto (aceito) | Decisão 2: `eval_llm_model` independente com default `lm_studio_llm_model`; temperatura 0.0 no judge; documentado como limitação conhecida (limiar de regressão qualitativo, não absoluto). |
| RAGAS 0.2.15 com `datasets` 3.6.0 — incompatibilidade interna | Médio | Fail-fast com traceback limpo e `!= 0` (REQ-13); verificar import e `ragas.evaluate` no ar; requisitos já pinados. |
| Falha de rede do LM Studio durante avaliação (não no probe) | Médio | Probe inicial + logs por pergunta; exceção de RAGAS tratada → erro claro + `!= 0`; `--n` permite rodar subconjunto. |
| Custos/tempo: avaliação com 4 métricas sobre LLM local | Médio | `--n` (limite de perguntas), `eval_batch_size`, embeddings/LLM locais (sem custo). |
| Cold start mascarando degradação de geração | Médio | Contagem explícita de cold starts no relatório (CA-05/REQ-5); FAQ no relatório JSON. |
| Golden set versionado com dados realistas demais | Baixo | Apenas medicamentos/instituições fictícios (padrão já usado nos testes); revisão humana no PR. |
| Mudanças em `retriever.py`/`chain.py` não intencionais | Baixo | Reforçado na spec/design: **zero** alterações nesses módulos (Decisão 1). |

## Perguntas em aberto

Nenhuma (as 4 da story foram decididas e aprovadas no Checkpoint 1).

---

**APROVAÇÃO:** conforme decisão do Checkpoint 2.
