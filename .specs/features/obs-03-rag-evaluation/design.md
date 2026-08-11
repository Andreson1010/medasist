# Design — OBS-03: Avaliação RAG (RAGAS)

**Slug:** `obs-03-rag-evaluation`
**Escopo TLC:** large
**Base:** `spec.md` deste diretório (Decisões 1-4, REQ-1..REQ-15).

## 1. Visão geral do pacote

Novo pacote `src/medasist/evaluation/` com três módulos, mais script CLI e dados versionados:

```
src/medasist/evaluation/
├── __init__.py     # exports + __all__
├── dataset.py      # GoldenQuestion, GoldenSet, load_golden_set
└── metrics.py      # build_eval_llm, build_eval_embeddings, build_metrics, evaluate_golden_set

scripts/evaluate_rag.py            # CLI (argparse, probes fail-fast, relatório)
evals/dataset/golden_set.json      # golden set sintético versionado
evals/results/                     # relatórios de saída — gitignored
```

Princípios:
- **Somente leitura** no pipeline existente: `retrieve()` e `run_query()` são usados como estão. **Zero** mudanças em `retriever.py`, `chain.py`, `api/`.
- Offline: LLM/embeddings apontam só para `lm_studio_base_url`; nada de nuvem.
- Sem passagem pela API HTTP: o script chama as funções de biblioteca diretamente.

## 2. Módulo `dataset.py`

### 2.1 Schema do golden set

`evals/dataset/golden_set.json` — arquivo único, lista de objetos sob a chave `questions`:

```jsonc
{
  "version": "1.0.0",
  "description": "Golden set sintético para avaliação offline do MedAssist (sem dados reais).",
  "questions": [
    {
      "question": "Qual a dose inicial recomendada de Alphazol para adultos com hipertensão?",
      "reference_answer": "A dose inicial de Alphazol é 10 mg/dia, ajustável conforme resposta clínica.",
      "reference_contexts": [
        "Alphazol X: dose inicial de 10 mg/dia para hipertensão arterial sistêmica."
      ],
      "doc_types": ["bula"],
      "profile": "medico",
      "is_cold_start": false
    }
  ]
}
```

| Campo (por pergunta) | Tipo | Obrigatório | Observações |
|---|---|---|---|
| `question` | `str` | sim | Não vazia; `strip()` antes de validar. |
| `reference_answer` | `str` | sim | Ground truth para ContextRecall/ContextPrecision. Não vazia. |
| `reference_contexts` | `list[str]` | não | Default `[]`; usado por RAGAS se presente. |
| `doc_types` | `list[str]` | não | Valores de `DocType` (`bula|diretriz|protocolo|manual`); default `[]` (todas). |
| `profile` | `str` | não | Valores de `UserProfile`; default `"medico"`. |
| `is_cold_start` | `bool` | não | Default `false`; flag explícita — ver §2.3. |

Top-level: `version: str` (obrigatório), `description: str` (obrigatório), `questions: list` (não vazio).

### 2.2 Modelos Pydantic

```python
class GoldenQuestion(BaseModel):
    question: str
    reference_answer: str
    reference_contexts: list[str] = []
    doc_types: list[DocType] = []
    profile: UserProfile = UserProfile.MEDICO
    is_cold_start: bool = False

class GoldenSet(BaseModel):
    version: str
    description: str
    questions: list[GoldenQuestion]  # validação: não vazio
```

Validações:
- `question` / `reference_answer`: rejeitar strings só de whitespace (erro com o nome do campo e o índice da pergunta).
- `doc_types`: valores fora de `DocType` → `ValidationError` listando o valor inválido.
- `profile`: fora de `UserProfile` → `ValidationError`.
- `questions` vazio → erro claro (CA-08 Edge).
- `is_cold_start` do golden set é **apenas documental** (indica a intenção de uma pergunta negativa); a flag efetiva que determina exclusão de métricas de geração é a **`is_cold_start` retornada pelo pipeline** (ver §4.3).

### 2.3 `load_golden_set`

```python
def load_golden_set(path: Path) -> GoldenSet:
    """Carrega e valida o golden set, retornando o modelo validado."""
```

- Lê o arquivo; `json.JSONDecodeError` → `ValueError("golden set malformado em <path>: <msg>")`.
- `GoldenSet.model_validate(data)` → em `ValidationError`, reescreve como `ValueError` **descritivo com campo + índice** (REQ-10): ex. `"pergunta 3: question não pode ser vazio"` ou `"pergunta 5: doc_types contém 'nota-fiscal' (inválido)"`.
- Converte para `datasets.Dataset` via `from_list` com colunas: `question`, `contexts` (listas vazias — preenchidas em `evaluate_golden_set`), `reference_answer`, `reference_contexts`, `is_cold_start` (bool do golden set).
- Retorna `GoldenSet` (o `datasets.Dataset` é montado pelo chamador com as respostas; ver §4). API alternativa: retornar o `Dataset` com placeholder e um segundo helper — **decisão de implementação**; preferência: `load_golden_set` retorna `GoldenSet`; `metrics.py` constrói o `Dataset` final.

## 3. Módulo `metrics.py`

### 3.1 Builders

```python
def build_eval_llm(settings: Settings) -> LangchainLLMWrapper:
    # ChatOpenAI(base_url=settings.lm_studio_base_url,
    #            api_key=settings.lm_studio_api_key.get_secret_value(),
    #            model=settings.eval_llm_model, temperature=0.0)
    # → LangchainLLMWrapper(...)

def build_eval_embeddings(settings: Settings) -> LangchainEmbeddingsWrapper:
    # OpenAIEmbeddings(base_url=settings.lm_studio_base_url, ...,
    #                  model=settings.eval_embedding_model,
    #                  check_embedding_ctx_length=False)
    # → LangchainEmbeddingsWrapper(...)

def build_metrics() -> list[Metric]:
    return [ContextPrecision(), ContextRecall(), Faithfulness(), AnswerRelevancy()]
```

- `eval_llm_model` default = `lm_studio_llm_model` (Decisão 2). `eval_embedding_model` default = `lm_studio_embedding_model`.
- `temperature=0.0` no judge para determinismo (as métricas de judge fazem chamadas LLM).

### 3.2 `evaluate_golden_set`

```python
def evaluate_golden_set(
    questions: list[GoldenQuestion],
    stores: dict[DocType, Chroma],
    settings: Settings,
    profile: UserProfile | None = None,
    doc_types: list[DocType] | None = None,
    top_k: int | None = None,
    batch_size: int | None = None,
) -> EvaluationResult
```

Fluxo:

1. **Settings efetivos:** se `top_k` informado → `settings = settings.model_copy(update={"retrieval_top_k": top_k})`; caso contrário usa `settings` como está. `batch_size` default = `settings.eval_batch_size`.
2. **`contexts` por pergunta:** `retrieve(question, stores, settings)` → `[d.page_content for d in docs]` (docs já ordenados por distância L2 e filtrados pelo threshold). `run_query` chama o retriever internamente — a avaliação **não** re-executa retrieval separadamente para montar contexts (a resposta já veio do mesmo retrieval). Consequência de projeto: contexts recuperados e contexts do answer são **idênticos** (mesmo `stores`/`settings`), garantindo consistência para as 4 métricas.
3. **`answer` + `is_cold_start` por pergunta:** `run_query(question, stores, profile, settings, doc_types)` → `GenerationResult`; usa `result.answer`, `result.is_cold_start`. Respeita `doc_types` do golden set por pergunta quando `doc_types` global não foi passado (o mais específico vence — decisão: o `doc_types` global do CLI tem precedência).
4. **Particionamento cold start (REQ-5, estratégia 2 — preferida):**
   - **Retrieval set:** todas as perguntas (ContextPrecision/ContextRecall valem mesmo com 0 contexts; contextos vazios → pontuação 0, sinalizando perda de retrieval).
   - **Geração set:** apenas perguntas com `is_cold_start == False` (Faithfulness/AnswerRelevancy).
   - Roda `ragas.evaluate` duas vezes: uma por subconjunto, com `metrics` adequadas. Resultado final: dicionário com agregadas por subconjunto + contagens.
5. **Erro de RAGAS:** exceção propagada com mensagem limpa (o script faz `logger.error` + retorno `1` — REQ-13).

### 3.3 Estrutura de resultado

Semântica do retorno (escolha de implementação — dataclass ou dict tipado, evitar Pydantic se não precisar de validação):

```python
@dataclass(frozen=True)
class QuestionEvalRow:
    question: str
    contexts: list[str]
    answer: str
    is_cold_start: bool
    metrics: dict[str, float]   # metric → score (pode ter None se não avaliada)

@dataclass(frozen=True)
class EvaluationReport:
    aggregates: dict[str, float]        # média por métrica (sobre os respectivos subconjuntos)
    per_question: list[QuestionEvalRow]
    num_questions: int
    num_cold_start: int
    num_generation_evaluated: int
```

## 4. Módulo `__init__.py`

Exports públicos + `__all__`:
`GoldenQuestion`, `GoldenSet`, `load_golden_set`, `build_eval_llm`, `build_eval_embeddings`, `build_metrics`, `evaluate_golden_set`, e o tipo de relatório escolhido em §3.3.

## 5. Script `scripts/evaluate_rag.py`

### 5.1 CLI contract

```
python scripts/evaluate_rag.py [--dataset PATH] [--top-k N] [--n N]
                               [--profile {medico,enfermeiro,assistente,paciente}]
                               [--doc-types {bula,diretriz,protocolo,manual} ...]
                               [--output PATH]
```

| Flag | Default | Descrição |
|---|---|---|
| `--dataset` | `settings.eval_golden_set_path` | Caminho do golden set. |
| `--top-k` | `settings.retrieval_top_k` | Sobrescreve `retrieval_top_k` (via `model_copy`). |
| `--n` | `None` (todos) | Limita o nº de perguntas avaliadas. |
| `--profile` | `medico` | Perfil de geração/judge. |
| `--doc-types` | `None` (todas) | Filtra coleções (nargs="+"). |
| `--output` | `None` | Grava relatório JSON (não versionado — `evals/results/`). |

### 5.2 Ordem de execução (fail-fast, CA-04/REQ-4)

1. `parse_args` → validação de choices.
2. `load_golden_set(args.dataset)` → dataset inválido/malformado/vazio → erro claro + `return 1`.
3. **Probe LM Studio:** `httpx.get(f"{settings.lm_studio_base_url}/models", timeout=settings.healthcheck_timeout)` (espelha `api/health.py:check_lm_studio`). Falha/timeout/não-2xx → `logger.error` + `return 1`. **Sem timeout longo.**
4. **Probe coleções:** `client = get_client(settings)`; `stores = get_all_vectorstores(client, build_embeddings(settings), settings)`; filtra por `--doc-types`; para cada store selecionada, `store._collection.count()` (ou `.count()` se exposto) — **coleção ausente ou vazia → erro claro + `return 1`** (REQ-11).
5. Aplica `--n` (slice das perguntas).
6. `evaluate_golden_set(...)` → `EvaluationReport`.
7. **Saída stdout:** agregadas (uma linha por métrica) + tabela por pergunta (question | is_cold_start | metrics). Relatório JSON se `--output`.
8. **Exit code:** `0` sucesso; `1` falha. Regra adicional: se **nenhuma** pergunta não-cold-start existir (geração set vazio) → reporta e `return 1` (REQ-12) — não há o que avaliar em geração.

Convenção seguida de `ingest_docs.py`: `from __future__ import annotations`, `parse_args(argv)->Namespace`, `main(argv)->int`, `sys.exit(main())`, `logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")`, `logger` por módulo. O relatório ao stdout usa `print()` apenas no CLI (saída do programa), aceito pois `scripts/` não é biblioteca; todo o resto via `logger`.

### 5.3 Schema do relatório JSON (`--output`)

```jsonc
{
  "generated_at": "2026-08-11T10:00:00Z",
  "dataset": "evals/dataset/golden_set.json",
  "profile": "medico",
  "doc_types": [],
  "aggregates": {
    "context_precision": 0.72,
    "context_recall": 0.65,
    "faithfulness": 0.81,
    "answer_relevancy": 0.78
  },
  "counts": { "questions": 9, "cold_start": 2, "generation_evaluated": 7 },
  "per_question": [
    {
      "question": "Qual a dose inicial de Alphazol...?",
      "is_cold_start": false,
      "contexts": ["Alphazol X: dose inicial de 10 mg/dia..."],
      "answer": "A dose inicial de Alphazol é 10 mg/dia [1].",
      "metrics": {
        "context_precision": 1.0,
        "context_recall": 1.0,
        "faithfulness": 0.9,
        "answer_relevancy": 0.85
      }
    }
  ]
}
```

- Sem scores de retrieval (fora de escopo).
- `aggregates` = média sobre o subconjunto onde a métrica foi avaliada.

## 6. Config (novas settings)

```python
eval_golden_set_path: Path = Field(default=Path("evals/dataset/golden_set.json"))
eval_llm_model: str = ""          # default vazio → resolver para lm_studio_llm_model
eval_embedding_model: str = ""    # default vazio → resolver para lm_studio_embedding_model
eval_batch_size: int = Field(default=16, gt=0)
```

- Decisão de implementação: `eval_llm_model`/`eval_embedding_model` com default vazio e `model_validator` que preenche com `lm_studio_llm_model`/`lm_studio_embedding_model` (assim o default acompanha a config do LLM principal). Alternativa: `model_validator(mode="after")` no `Settings`. Manter o default **vazio** evita valor duplicado "phi-3-mini" fixo.
- `.env.example`:
  ```
  # Avaliação RAG (offline)
  EVAL_GOLDEN_SET_PATH=evals/dataset/golden_set.json
  EVAL_LLM_MODEL=                # vazio → usa LM_STUDIO_LLM_MODEL
  EVAL_EMBEDDING_MODEL=          # vazio → usa LM_STUDIO_EMBEDDING_MODEL
  EVAL_BATCH_SIZE=16
  ```

## 7. Golden set `evals/dataset/golden_set.json`

~8-10 perguntas sintéticas PT-BR, cobrindo:
- 4-5 bulas de medicamentos fictícios (Alphazol, Zolatril, Betanorm, Ceflunex — nomes já usados nos fixtures de teste; sem marca real).
- 2-3 diretrizes/protocolos fictícios (ex: "protocolo de manejo de cefaleia tensiona no pronto-atendimento").
- 1-2 perguntas com `is_cold_start: true` (pergunta sobre tema fora do corpus, ex: "Como tratar pneumonia fúngica em camaleões?") para validar CA-05/REQ-5.
- `reference_answer` factual, citável; `reference_contexts` opcionais.
- Nenhum dado de paciente, instituição real ou medicamento real.

## 8. Testes

Espelham `src/` → `tests/`; fixtures sintéticos; mocks via `pytest-mock`; embeddings fake (`_FakeEmbeddings`) + `chromadb.EphemeralClient` para vectorstore (padrão de `tests/retrieval/test_retriever.py`).

| Arquivo de teste | Cobre |
|---|---|
| `tests/evaluation/test_dataset.py` | `load_golden_set`: JSON válido → `GoldenSet`; malformado (JSONDecodeError); `question` vazio; `doc_types` inválido; `questions` vazio; erro com campo+índice. |
| `tests/evaluation/test_metrics.py` | `build_eval_llm`/`build_eval_embeddings` (apontam para `lm_studio_base_url` e usam `eval_*_model`); `build_metrics` (4 métricas); `evaluate_golden_set` com `run_query`/`retrieve` mockados: cold start excluído da geração, contagens, agregadas, `top_k` override via `model_copy`. |
| `tests/scripts/test_evaluate_rag.py` | `parse_args` (defaults/choices); `main`: dataset inválido → 1; LM Studio fora (patch httpx) → 1; coleções vazias → 1; sucesso → 0 (patch `evaluate_golden_set`); `--n` slice; `--output` grava JSON. |
| `tests/acceptance/test_obs_03_evaluation.py` | Aceite de ponta a ponta patcheando **apenas boundaries** (padrão obs-02): `datasets`/`ragas.evaluate` mockados ou `httpx` para o probe; executa `evaluate_rag.main()` com stores reais efêmeras + `_FakeEmbeddings` para CA-01..CA-07 (ver abaixo). |

### 8.1 Aceite por CA

- **CA-01** `load_golden_set` num golden set válido em `tmp_path`; schema inválido → erro descritivo com campo.
- **CA-02** `evaluate_rag.main([...])` com `ragas.evaluate` mockado (ou RAGAS real com judge mockado) → stdout com métricas, exit 0.
- **CA-03** `build_eval_llm`/`build_eval_embeddings` inspecionados: `base_url == settings.lm_studio_base_url`; nenhuma chamada a endpoint externo (mock de `ChatOpenAI`/`OpenAIEmbeddings` para nunca tocar a rede).
- **CA-04** probe LM Studio com `httpx.get` mockado levantando `ConnectError` → `main` retorna 1, sem chamada a `evaluate_golden_set`.
- **CA-05** stores reais efêmeras com 1 doc relevante + 1 pergunta fora do corpus → resultado com `num_cold_start >= 1` e pergunta cold start ausente das métricas de geração.
- **CA-06** casos de saída: dataset inválido → 1; coleção vazia → 1; LM Studio fora → 1; sucesso → 0.
- **CA-07** prova de que não passa pela API: o teste chama `evaluate_rag.main`/`evaluate_golden_set` **sem** TestClient; verifica que `retrieve`/`run_query` foram chamados (spy) e nenhum request HTTP a `/query`.

## 9. Dependências entre módulos

```
config.py (4 settings)
   └─► evaluation/dataset.py (GoldenSet/GoldenQuestion/load_golden_set)
        └─► evaluation/metrics.py (builders + evaluate_golden_set)
             └─► evaluation/__init__.py (exports)
                  └─► scripts/evaluate_rag.py
                       └─► evals/dataset/golden_set.json
```

`evaluate_golden_set` depende de `retrieve` (retriever) e `run_query` (chain) — **sem modificá-los**.

## 10. Fora de escopo (confirmado)

- Endpoint de avaliação na API; CI integrado; dados reais; citações com seção/página; métricas além das 4; exposição de scores de retrieval; alteração de `retriever.py`/`chain.py`/`api/`.
