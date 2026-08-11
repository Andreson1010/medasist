# Tasks — OBS-03: Avaliação RAG (RAGAS)

**Slug:** `obs-03-rag-evaluation`
**Escopo TLC:** large
**Base:** `spec.md` (REQ-1..REQ-15) + `design.md`.

Notação: `[P]` = tarefa que pode rodar em paralelo com as demais do mesmo nível.
`Gate` = comando de verificação que deve passar para a tarefa ser considerada feita.

## Fase 0 — Preparação

### T0.1 — Branch do feature
- **What:** Criar branch `feat/obs-03-rag-evaluation` no worktree.
- **Where:** repo raiz / worktree `obs-03-rag-evaluation`.
- **Depends on:** —
- **Done when:** `git branch --show-current` == `feat/obs-03-rag-evaluation`.
- **Tests:** — (git)
- **Gate:** `git status --short` limpo; `git branch --show-current`.

## Fase 1 — Configuração (base para todos)

### T1.1 — Novas settings de avaliação
- **What:** Adicionar a `Settings` (`src/medasist/config.py`): `eval_golden_set_path: Path = Field(default=Path("evals/dataset/golden_set.json"))`, `eval_llm_model: str = ""`, `eval_embedding_model: str = ""`, `eval_batch_size: int = Field(default=16, gt=0)`. Resolver `eval_llm_model`/`eval_embedding_model` vazios para `lm_studio_llm_model`/`lm_studio_embedding_model` via `model_validator(mode="after")`. Docstrings NumPy dos novos campos.
- **Where:** `src/medasist/config.py`
- **Depends on:** T0.1
- **Done when:** `Settings()` reflete defaults; `model_copy(update={"eval_llm_model": ""})` resolve para `lm_studio_llm_model`.
- **Tests:** ampliar `tests/config/test_config.py`: defaults dos 4 campos; resolução dos modelos vazios; `eval_batch_size` rejeita `<= 0`.
- **Gate:** `python -m pytest tests/config/test_config.py -v`; `ruff check src/medasist/config.py`; `black --check src/medasist/config.py`.

### T1.2 — `.env.example` documenta settings de avaliação
- **What:** Adicionar bloco "Avaliação RAG (offline)" com `EVAL_GOLDEN_SET_PATH`, `EVAL_LLM_MODEL`, `EVAL_EMBEDDING_MODEL`, `EVAL_BATCH_SIZE` (ver `design.md` §6).
- **Where:** `.env.example`
- **Depends on:** T1.1
- **Done when:** variáveis documentadas com os mesmos defaults/comentários do `design.md`.
- **Tests:** — (arquivo de exemplo)
- **Gate:** leitura visual; `git diff .env.example`.

### T1.3 — `.gitignore` ignora `evals/results/`
- **What:** Adicionar `evals/results/` (relatórios de saída) ao `.gitignore`.
- **Where:** `.gitignore`
- **Depends on:** T0.1
- **Done when:** `git check-ignore evals/results/` retorna o path.
- **Tests:** — (git)
- **Gate:** `git check-ignore evals/results/`.

## Fase 2 — Pacote `evaluation` (paralelo a Fase 3)

### T2.1 — `dataset.py`: modelos Pydantic + `load_golden_set`
- **What:** Implementar `GoldenQuestion`, `GoldenSet`, `load_golden_set(path) -> GoldenSet` conforme `design.md` §2 (validação: question/reference_answer não vazios, doc_types/profile válidos, questions não vazio; JSONDecodeError e ValidationError traduzidos em `ValueError` descritivo com campo + índice; conversão para `datasets.Dataset.from_list` com colunas `question`, `contexts`, `reference_answer`, `reference_contexts`, `is_cold_start`).
- **Where:** `src/medasist/evaluation/dataset.py` (novo)
- **Depends on:** T1.1
- **Done when:** REQ-1, REQ-9, REQ-10, REQ-15 atendidos; `load_golden_set` retorna `GoldenSet` validado; conversão para Dataset sem erro.
- **Tests:** `tests/evaluation/test_dataset.py`: JSON válido; malformado; `question` vazio (campo+índice no erro); `doc_types` inválido; `questions` vazio; `reference_answer` só whitespace; profile inválido.
- **Gate:** `python -m pytest tests/evaluation/test_dataset.py -v`; `ruff check`; `black --check`.

### T2.2 — `metrics.py`: builders de LLM/embeddings/metrics
- **What:** Implementar `build_eval_llm(settings)` (`ChatOpenAI`→LM Studio→`LangchainLLMWrapper`, `temperature=0.0`, `model=eval_llm_model`), `build_eval_embeddings(settings)` (`OpenAIEmbeddings`→LM Studio→`LangchainEmbeddingsWrapper`, `model=eval_embedding_model`), `build_metrics()` (as 4 métricas). REQ-3.
- **Where:** `src/medasist/evaluation/metrics.py` (novo)
- **Depends on:** T1.1
- **Done when:** builders retornam instâncias RAGAS apontando só para `lm_studio_base_url`; `build_metrics` retorna 4 métricas.
- **Tests:** `tests/evaluation/test_metrics.py`: wrappers construídos com `base_url`/model corretos (patch de `ChatOpenAI`/`OpenAIEmbeddings` para não tocar rede); `build_metrics` len==4 e tipos esperados.
- **Gate:** `python -m pytest tests/evaluation/test_metrics.py -v`; `ruff check`; `black --check`.

### T2.3 — `metrics.py`: `evaluate_golden_set` + relatório
- **What:** Implementar `evaluate_golden_set(questions, stores, settings, profile, doc_types, top_k, batch_size)` conforme `design.md` §3.2/§3.3: contexts via `retrieve`, answer+is_cold_start via `run_query`, particionamento cold start (2 execuções de `ragas.evaluate`: retrieval-set completo, geração-set sem cold starts), agregadas, `QuestionEvalRow`/`EvaluationReport`. REQ-2, REQ-5, REQ-7, REQ-12.
- **Where:** `src/medasist/evaluation/metrics.py`
- **Depends on:** T2.2
- **Done when:** chama `retrieve`/`run_query` (nunca API HTTP); cold start excluído da geração com contagem; `top_k` override via `model_copy`; todas-as-cold-start → geração-set vazio sinalizado.
- **Tests:** `tests/evaluation/test_metrics.py` (parte 2): mock `retrieve`/`run_query`; cenário com 1 cold start e 2 normais → `num_cold_start==1`, `num_generation_evaluated==2`, métricas de geração só nas 2; `top_k` propagado; `run_query` chamado com profile/doc_types.
- **Gate:** `python -m pytest tests/evaluation/test_metrics.py -v`; `ruff check`; `black --check`.

### T2.4 — `__init__.py`: exports
- **What:** Exportar `GoldenQuestion`, `GoldenSet`, `load_golden_set`, `build_eval_llm`, `build_eval_embeddings`, `build_metrics`, `evaluate_golden_set`, tipo de relatório; definir `__all__`.
- **Where:** `src/medasist/evaluation/__init__.py`
- **Depends on:** T2.1, T2.3
- **Done when:** `from medasist.evaluation import load_golden_set, evaluate_golden_set` funciona; ruff respeita `F401` per-file-ignore.
- **Tests:** teste de import no `test_metrics.py` ou novo.
- **Gate:** `ruff check src/medasist/evaluation/__init__.py`.

## Fase 3 — Dados e script (paralelo à Fase 2)

### T3.1 — Golden set `evals/dataset/golden_set.json` `[P]`
- **What:** Criar golden set com ~8-10 perguntas sintéticas PT-BR (5 bulas fictícias Alphazol/Zolatril/Betanorm/Ceflunex + 2-3 diretrizes/protocolos fictícios + 1-2 com `is_cold_start: true`), `version`, `description`, `reference_answer` para todas, `reference_contexts`/`doc_types`/`profile` opcionais conforme `design.md` §2.1/§7. Nenhum dado real.
- **Where:** `evals/dataset/golden_set.json` (novo)
- **Depends on:** T1.1 (para o formato), T0.1
- **Done when:** JSON válido; `load_golden_set` valida sem erro; REQ-8/REQ-14.
- **Tests:** teste manual via `python -c "from medasist.evaluation.dataset import load_golden_set; ..."`; + caso no `test_dataset.py` apontando para o arquivo versionado.
- **Gate:** `python -m pytest tests/evaluation/test_dataset.py -v` (inclui teste do arquivo real).

### T3.2 — Script `scripts/evaluate_rag.py` `[P]`
- **What:** Implementar `parse_args`/`main` conforme `design.md` §5: flags `--dataset`, `--top-k`, `--n`, `--profile`, `--doc-types`, `--output`; fail-fast (dataset → probe LM Studio httpx com `healthcheck_timeout` → coleções com count>0); execução `evaluate_golden_set`; stdout com agregadas + tabela por pergunta; relatório JSON opcional (`design.md` §5.3); exit codes 0/1.
- **Where:** `scripts/evaluate_rag.py` (novo)
- **Depends on:** T2.3, T3.1
- **Done when:** REQ-2, REQ-4, REQ-6, REQ-8..REQ-13 atendidos; segue convenção `ingest_docs.py` (argparse, `main()->int`, `sys.exit(main())`, logging).
- **Tests:** `tests/scripts/test_evaluate_rag.py`: `parse_args` defaults/choices; dataset inválido→1; `httpx.get` mockado com ConnectError→1 (e `evaluate_golden_set` não chamado); coleção vazia→1; sucesso→0 (patch `evaluate_golden_set`); `--n`; `--output` grava JSON.
- **Gate:** `python -m pytest tests/scripts/test_evaluate_rag.py -v`; `ruff check scripts/`; `black --check scripts/`.

### T3.3 — Docs: CLAUDE.md + README `[P]`
- **What:** Linha "Avaliação RAG" nos Comandos Comuns do `CLAUDE.md` (`python scripts/evaluate_rag.py --dataset evals/dataset/golden_set.json`) e menção/sessão curta no `README.md` (offline, sem API, 4 métricas, requer LM Studio + coleções populadas).
- **Where:** `CLAUDE.md`, `README.md`
- **Depends on:** T3.2 (comando real)
- **Done when:** comandos documentados conferem com o CLI implementado.
- **Tests:** — (docs)
- **Gate:** leitura visual; `git diff CLAUDE.md README.md`.

## Fase 4 — Testes de aceitação + gate completo

### T4.1 — Testes de aceitação OBS-03
- **What:** `tests/acceptance/test_obs_03_evaluation.py` patcheando apenas boundaries (padrão obs-02): CA-01 (load golden set válido/ inválido), CA-02 (exit 0 + stdout com métricas, `ragas.evaluate` mockado), CA-03 (builders apontam para LM Studio; sem chamada externa), CA-04 (LM Studio fora → 1), CA-05 (cold start excluído da geração, stores efêmeras + `_FakeEmbeddings`), CA-06 (exit codes), CA-07 (chama `main`/`evaluate_golden_set` sem TestClient; spy em `retrieve`/`run_query`; nenhum request a `/query`).
- **Where:** `tests/acceptance/test_obs_03_evaluation.py` (novo)
- **Depends on:** T2.4, T3.2, T3.1
- **Done when:** todos os CAs verdes; nenhum arquivo `src/` alterado só para o teste.
- **Tests:** o próprio arquivo.
- **Gate:** `python -m pytest tests/acceptance/test_obs_03_evaluation.py -v`.

### T4.2 — Gate completo (black + ruff + pytest + cobertura)
- **What:** Rodar full gate.
- **Where:** repo.
- **Depends on:** T4.1 (e todo o resto)
- **Done when:** `black --check src/ tests/ scripts/` limpo; `ruff check src/ tests/ scripts/` limpo; `pytest tests/ -v --cov=src --cov-fail-under=80` verde.
- **Tests:** — (gate)
- **Gate:**
  ```
  black --check src/ tests/ scripts/
  ruff check src/ tests/ scripts/
  python -m pytest tests/ -v --cov=src --cov-fail-under=80
  ```

## Fase 5 — Encerramento

### T5.1 — Validação manual smoke (opcional, com LM Studio)
- **What:** Com LM Studio up e coleções populadas, rodar `python scripts/evaluate_rag.py --n 3` e conferir saída + exit 0.
- **Where:** CLI.
- **Depends on:** T4.2
- **Done when:** stdout com métricas agregadas/per pergunta; `echo $LASTEXITCODE` == 0.
- **Tests:** — (smoke)
- **Gate:** manual.

### T5.2 — Commit atômico e PR
- **What:** Commits em português/imperativo (`feat: adiciona avaliação RAG offline com RAGAS`); separar: config/env/gitignore → evaluation package → golden set → script+docs → testes. Abrir PR `feat/obs-03-rag-evaluation` após code review (skill code-reviewer).
- **Where:** git.
- **Depends on:** T4.2
- **Done when:** PR aberto com todos os commits revisados.
- **Tests:** — (git/PR)
- **Gate:** `git log --oneline` coerente; PR sem segredos.

---

## Grafo de dependências

```
T0.1
 ├─ T1.1 ─┬─ T1.2
 │        └─ T2.1 ── T2.2 ── T2.3 ── T2.4 ──┬─ T4.1 ── T4.2 ── T5.1 ── T5.2
 ├─ T1.3                                      │
 ├─ T3.1 ─────────────────────────────────────┘  (T3.1 paralelo a Fase 2)
 └─ T3.2 ── T3.3
      (T3.2 depende de T2.3; T3.3 depende de T3.2)
```

## Rastreabilidade REQ → Tasks

| REQ | Tasks | REQ | Tasks |
|---|---|---|---|
| REQ-1 | T2.1, T4.1 | REQ-9 | T2.1, T3.2 |
| REQ-2 | T2.3, T3.2, T4.1 | REQ-10 | T2.1, T3.2 |
| REQ-3 | T2.2, T4.1 | REQ-11 | T3.2, T4.1 |
| REQ-4 | T3.2, T4.1 | REQ-12 | T2.3, T3.2 |
| REQ-5 | T2.3, T4.1 | REQ-13 | T2.3, T3.2 |
| REQ-6 | T3.2, T4.1 | REQ-14 | T3.1, T3.2 |
| REQ-7 | T2.3, T4.1 | REQ-15 | T2.1 |
| REQ-8 | T3.1, T3.2 | | |
