# Policy-as-Code — Tasks

**Design:** `.specs/features/policy-as-code/design.md`
**Spec:** `.specs/features/policy-as-code/spec.md`
**Status:** Awaiting human approval

Notas de gate (TESTING.md + pyproject.toml vigentes):
- Quick gate: `pytest tests/<alvo> -v`
- Full gate: `black src/ tests/ scripts/ && ruff check src/ tests/ scripts/ && pytest tests/ -v --cov=src --cov-fail-under=80`
- Cobertura: `addopts = "--cov=src --cov-report=term-missing --cov-fail-under=80"` e `pythonpath = ["src", "scripts"]` (pyproject.toml) — o módulo novo `policies/*` é medido; deve nascer com testes no mesmo PR.
- Padrão de teste de script: `from policy_check import ...` (via pythonpath), `mocker.patch`, argv lists, assert exit 0/1, `capsys`.
- Commits em PT-BR, imperativo, um por task (padrão AGENTS.md): `feat(policies): <descrição>`.

---

## Execution Plan

### Phase 1: Foundation (Sequential)

```
T1 → T2
```

### Phase 2: Regras + independents (Parallel OK)

```
T1 done, then:
     ┌→ T3 ─┐
     ├→ T4 ─┤
     ├→ T5 ─┤
T2 ──┼→ T6 ─┼──→ T10
     ├→ T7 ─┤
     ├→ T8 ─┤
     └→ T9 ─┘
T15 ──────→ (independent, sem deps)
T16 ──────→ (independent, sem deps)
```

### Phase 3: Integração (Sequential)

```
T10 → T11 → T12
```

### Phase 4: Gates de config (Parallel OK)

```
T12 ──→ T13 ─┐
T12 ──→ T14 ─┼──→ T18
T12 ──→ T17 ─┘
T15 ──────→
T16 ──────→
```

### Phase 5: Aceite (Sequential)

```
T18
```

---

## Task Breakdown

### T1: Criar value objects do relatório (`report.py`) + bootstrap do pacote

**What**: Dataclasses congeladas `PolicyViolation` e `PolicyReport` + `__init__.py` do pacote (docstring + future import, sem re-exports — padrão `monitoring/`).
**Where**: `src/medasist/policies/__init__.py`, `src/medasist/policies/report.py`, `tests/policies/__init__.py`, `tests/policies/test_report.py`
**Depends on**: None
**Reuses**: Padrão frozen dataclass (`ingestion/schemas.py`, `profiles/schemas.py`); `monitoring/__init__.py` para o `__init__.py` minimalista
**Requirement**: REQ-POL-01, REQ-POL-06

**Tools**:
- MCP: NONE
- Skill: build-with-tests

**Done when**:
- [ ] `PolicyViolation` com `rule_id, path, line, message, baseline=False, symbol=None` (frozen)
- [ ] `PolicyReport` com `violations, baseline_entries, obsolete_entries, files_scanned, files_skipped` + properties `new_violations`, `baselined_violations`, `total_violations`, `has_failures`
- [ ] `__init__.py` com docstring + `from __future__ import annotations` (primeira linha executável — a própria feature é exemplo)
- [ ] Testes unitários em `tests/policies/test_report.py` (construtores, properties, immutabilidade)
- [ ] Gate: `pytest tests/policies/test_report.py -v`
- [ ] Test count: todos os testes do arquivo passam (sem deleção silenciosa)

**Tests**: unit
**Gate**: quick
**Commit**: `feat(policies): adiciona value objects do relatório de política`

---

### T2: Modelo e IO da baseline (`baseline.py`)

**What**: `BaselineEntry` frozen + `load_baseline` (tomllib, ausente → vazia), `save_baseline` (serializador TOML interno mínimo), `normalize_path` (POSIX), `match` (chave rule_id+path+location; active=False ignora), `find_obsolete`.
**Where**: `src/medasist/policies/baseline.py`, `tests/policies/test_baseline.py`
**Depends on**: T1
**Reuses**: `Path` conventions; `tomllib` stdlib
**Requirement**: REQ-POL-02, REQ-POL-14, REQ-POL-15, REQ-POL-18

**Tools**:
- MCP: NONE
- Skill: build-with-tests

**Done when**:
- [ ] `load_baseline` lê `policies.toml` de teste e retorna tupla; arquivo ausente → tupla vazia; TOML inválido → levanta
- [ ] `save_baseline` escreve TOML que `load_baseline` relê com round-trip idêntico (escape de `\` e `"`)
- [ ] `normalize_path` converte `C:\proj\src\x.py` → `src/x.py`
- [ ] `match` casa por `(rule_id, path, location)`; `active=False` nunca suprime
- [ ] `find_obsolete` detecta entrada ativa sem violação correspondente (violação corrigida, símbolo removido, arquivo deletado)
- [ ] Gate: `pytest tests/policies/test_baseline.py -v`

**Tests**: unit
**Gate**: quick
**Commit**: `feat(policies): adiciona baseline/allowlist com detecção de entradas obsoletas`

---

### T3: Regra future-import [P]

**What**: `check_future_import` via AST — primeira linha executável não-docstring deve ser `from __future__ import annotations`; vazio/`__init__.py` sem conteúdo executável isento.
**Where**: `src/medasist/policies/rules.py` (função), `tests/policies/test_rules_future_import.py`
**Depends on**: T1
**Reuses**: `ast` stdlib; decisão OQ-02 (design.md)
**Requirement**: REQ-POL-03, REQ-POL-07, REQ-POL-20

**Tools**:
- MCP: NONE
- Skill: build-with-tests

**Done when**:
- [ ] Docstring de módulo antes do future import → sem violação (ex.: texto de `src/medasist/monitoring/__init__.py`)
- [ ] Comentários antes → sem violação; código executável antes → violação; ausente → violação
- [ ] Arquivo vazio ou `__init__.py` só-comentário → isento
- [ ] Arquivo só-docstring → isento (sem future import)
- [ ] Gate: `pytest tests/policies/test_rules_future_import.py -v`

**Tests**: unit
**Gate**: quick
**Commit**: `feat(policies): adiciona regra de future import`

---

### T4: Regra pathlib [P]

**What**: `check_pathlib` — string literal como path bruto onde pathlib é a convenção (`open("...")`, separador/extensão de arquivo fora de `Path(...)`/`str(...)`); docstring de módulo, URLs e placeholders ignorados.
**Where**: `src/medasist/policies/rules.py` (função + `_PATH_EXTENSIONS`), `tests/policies/test_rules_pathlib.py`
**Depends on**: T1
**Reuses**: Convenção 100% pathlib do repositório (CONVENTIONS.md "Paths")
**Requirement**: REQ-POL-08

**Tools**:
- MCP: NONE
- Skill: build-with-tests

**Done when**:
- [ ] `open("data/x.pdf")` e `path = "C:\\x\\y"` → violação
- [ ] `Path("data/x.pdf")`, `str(path)` → sem violação
- [ ] Docstring de módulo com caminho, URL (`https://...`), `%s`/f-string placeholder → sem violação
- [ ] Gate: `pytest tests/policies/test_rules_pathlib.py -v`

**Tests**: unit
**Gate**: quick
**Commit**: `feat(policies): adiciona regra de pathlib`

---

### T5: Regra no-print + carve-out [P]

**What**: `check_no_print` via AST — chamadas `print(...)` → violação; suppress por allowlist `(path, símbolo_enclosing)`; `sys.stdout.write`/`logger.*` não flaggados.
**Where**: `src/medasist/policies/rules.py` (função), `tests/policies/test_rules_no_print.py`
**Depends on**: T1, T2 (usa `BaselineEntry` para permitir testar allowlist)
**Reuses**: decisão OQ-03 (design.md); contrato `capsys` de `tests/scripts/test_evaluate_rag.py`
**Requirement**: REQ-POL-09, REQ-POL-18

**Tools**:
- MCP: NONE
- Skill: build-with-tests

**Done when**:
- [ ] `print("x")` em `src/`/`tests/`/`scripts/` fora da allowlist → violação
- [ ] Entrada `(scripts/evaluate_rag.py, _print_report)` na allowlist → `print()`s do `_print_report` não geram violação
- [ ] `print()` novo em outro símbolo do mesmo arquivo → violação
- [ ] `sys.stdout.write(...)` → sem violação
- [ ] Gate: `pytest tests/policies/test_rules_no_print.py -v`

**Tests**: unit
**Gate**: quick
**Commit**: `feat(policies): adiciona regra de no-print com carve-out de stdout de CLI`

---

### T6: Regra logger [P]

**What**: `check_logger` via AST — módulos sob root `src/` com conteúdo executável exigem `logger = logging.getLogger(__name__)`; `__init__.py` vazio isento.
**Where**: `src/medasist/policies/rules.py` (função), `tests/policies/test_rules_logger.py`
**Depends on**: T1
**Reuses**: Convenção universal de logger (CONVENTIONS.md "Logging")
**Requirement**: REQ-POL-10

**Tools**:
- MCP: NONE
- Skill: build-with-tests

**Done when**:
- [ ] Módulo `src/...` com código e sem `logger = ...` → violação
- [ ] `logger = logging.getLogger(__name__)` presente → sem violação
- [ ] Módulo fora de `src/` (ex.: `tests/`, `scripts/`) → regra não aplica
- [ ] `__init__.py` vazio em `src/` → isento (não gera baseline)
- [ ] Gate: `pytest tests/policies/test_rules_logger.py -v`

**Tests**: unit
**Gate**: quick
**Commit**: `feat(policies): adiciona regra de logger em src/`

---

### T7: Regra docstring [P]

**What**: `check_docstring` — símbolos públicos (top-level + métodos, sem `_` inicial, sem dunder) exigem docstring; privados/dunders/vazios isentos.
**Where**: `src/medasist/policies/rules.py` (função), `tests/policies/test_rules_docstring.py`
**Depends on**: T1
**Reuses**: Convenção NumPy PT-BR (CONVENTIONS.md "Docstrings"); decisão de isentar privados (design.md — 3 helpers pré-existentes sem docstring ficam de fora)
**Requirement**: REQ-POL-11, REQ-POL-20

**Tools**:
- MCP: NONE
- Skill: build-with-tests

**Done when**:
- [ ] Função pública sem docstring → violação; com docstring → sem violação
- [ ] `_privada` sem docstring → isento; `__dunder__` → isento
- [ ] Método público de classe sem docstring → violação
- [ ] `__init__.py` vazio → isento
- [ ] Gate: `pytest tests/policies/test_rules_docstring.py -v`

**Tests**: unit
**Gate**: quick
**Commit**: `feat(policies): adiciona regra de docstring para símbolos públicos`

---

### T8: Regra de complexidade (50/4/800) [P]

**What**: `check_complexity` — FUNC-LENGTH (span físico do `def`, inclusivo, >50 viola), NESTING-DEPTH (>4 viola), FILE-LENGTH (>800 viola); CRLF-safe.
**Where**: `src/medasist/policies/rules.py` (função + sub-checks), `tests/policies/test_rules_complexity.py`
**Depends on**: T1
**Reuses**: Limites AD-005; atenção a CRLF (Windows) no design
**Requirement**: REQ-POL-12, REQ-POL-19

**Tools**:
- MCP: NONE
- Skill: build-with-tests

**Done when**:
- [ ] Função de 50 linhas → sem violação; 51 → violação (limite inclusivo)
- [ ] Aninhamento 4 → sem violação; 5 → violação
- [ ] Arquivo 800 linhas → sem violação; 801 → violação
- [ ] Texto com `\r\n` conta linhas corretamente (CRLF normalizado via `splitlines`)
- [ ] Gate: `pytest tests/policies/test_rules_complexity.py -v`

**Tests**: unit
**Gate**: quick
**Commit**: `feat(policies): adiciona regra de limites de código 50/4/800`

---

### T9: Regra de dado real de paciente [P]

**What**: `check_patient_data` — regex estáticos (CPF, RG, SUS, telefone, e-mail, DOB com range etário) + allowlist de tokens sintéticos; mensagem com o padrão casado.
**Where**: `src/medasist/policies/rules.py` (função + tabela `_PATIENT_DATA_PATTERNS`), `tests/policies/test_rules_patient_data.py`
**Depends on**: T1, T2 (allowlist de tokens)
**Reuses**: OQ-05 (design.md "Design do regex"); fixtures sintéticos (TESTING.md "Synthetic Data Approach")
**Requirement**: REQ-POL-13

**Tools**:
- MCP: NONE
- Skill: build-with-tests

**Done when**:
- [ ] CPF/RG/cartão SUS/telefone BR/e-mail/DOB plausível → violação com linha
- [ ] Nomes sintéticos (Zolatril, Alphazol, Betazol, Gammacol, amoxicilina, ibuprofeno, omeprazol, dipirona, paracetamol) → sem violação
- [ ] Data fora do range etário (ex.: `01/01/1900`) → sem violação
- [ ] Padrões ancorados não casam números embutidos (ex.: trecho de hash)
- [ ] Gate: `pytest tests/policies/test_rules_patient_data.py -v`

**Tests**: unit
**Gate**: quick
**Commit**: `feat(policies): adiciona regra de dado real de paciente`

---

### T10: Scanner (traversal + orquestração)

**What**: `collect_files` (DEFAULT_ROOTS `src tests scripts`, DEFAULT_EXCLUDES OQ-08, `rglob("*.py")`), `run_scan` (leitura CRLF-safe, registry de rules, match de baseline, `find_obsolete`), `generate_baseline` (para `--baseline-generate`), `RULE_REGISTRY` ordenado em `rules.py`.
**Where**: `src/medasist/policies/scanner.py`, `tests/policies/test_scanner.py`, `src/medasist/policies/rules.py` (registry)
**Depends on**: T2, T3, T4, T5, T6, T7, T8, T9
**Reuses**: `Path.rglob`; decisões OQ-08 (design.md)
**Requirement**: REQ-POL-01, REQ-POL-02, REQ-POL-06, REQ-POL-14, REQ-POL-15

**Tools**:
- MCP: NONE
- Skill: build-with-tests

**Done when**:
- [ ] `collect_files(["src"], excludes)` não desce em `.opencode/`, `.agents/`, `.git/`, `data/`, `evals/`, `chroma_db/`, `logs/`, `docs/`, `node_modules/`, `__pycache__`, ocultos; `--exclude` extra funciona
- [ ] `run_scan` retorna `PolicyReport` com violações novas vs baselinadas e `obsolete_entries` corretas
- [ ] `generate_baseline` produz entradas ativas a partir das violações atuais
- [ ] Arquivo `.py` ilegível → registrado em `files_skipped` (não silencioso)
- [ ] Gate: `pytest tests/policies/test_scanner.py -v`

**Tests**: unit
**Gate**: quick
**Commit**: `feat(policies): adiciona scanner com exclusões e orquestração de regras`

---

### T11: CLI `scripts/policy_check.py` + testes de script

**What**: CLI thin — bootstrap `sys.path` → `src`, `parse_args` (paths default, `--exclude`, `--baseline`, `--baseline-generate`), `main -> int` (0/1), `_render` via `sys.stdout.write`, `sys.exit(main())`; erros fatais via `logger.error`.
**Where**: `scripts/policy_check.py`, `tests/scripts/test_policy_check.py`
**Depends on**: T10
**Reuses**: Contrato `parse_args/main` de `scripts/evaluate_rag.py`/`scripts/ingest_docs.py`; padrão de teste de script (`from policy_check import ...`, capsys, mocker)
**Requirement**: REQ-POL-01, REQ-POL-06

**Tools**:
- MCP: NONE
- Skill: build-with-tests

**Done when**:
- [ ] `main([])` com árvore conforme + baseline → 0; relatório em `sys.stdout` via `capsys` contém regra/arquivo/local
- [ ] `main(["--baseline-generate"])` grava `policies.toml` e retorna 0
- [ ] `main` com violação não baselinada → 1; com baseline obsoleta → 1
- [ ] `--exclude` e paths posicionais repassados ao scanner
- [ ] Import funciona sem pacote instalado (bootstrap `sys.path` — teste simula execução direta)
- [ ] Gate: `pytest tests/scripts/test_policy_check.py -v`
- [ ] Test count: todos os testes do arquivo passam (sem deleção silenciosa)

**Tests**: unit
**Gate**: quick
**Commit**: `feat(policies): adiciona CLI policy_check com contrato de exit`

---

### T12: População da baseline (`policies.toml`) e 1º commit verde

**What**: Rodar `python scripts/policy_check.py --baseline-generate`, revisar o `policies.toml` gerado (deve conter **7 entradas FUNC-LENGTH** — `chain.py: _run_single/_merge_sub_results/_stream_single/stream_answer`, `retriever.py: retrieve/_retrieve_hybrid`, `pipeline.py: ingest_document` com reason `débito AD-005` — **+ 1 entrada NO-PRINT** — `scripts/evaluate_rag.py: _print_report` com reason carve-out OQ-03), commitá-lo, e verificar exit 0 na árvore.
**Where**: `policies.toml`
**Depends on**: T11
**Reuses**: Verificação contra a árvore real do worktree (design.md "Storage")
**Requirement**: REQ-POL-02, REQ-POL-14, REQ-POL-18

**Tools**:
- MCP: NONE
- Skill: build-with-tests

**Done when**:
- [ ] `policies.toml` contém exatamente as 8 entradas esperadas (sem entradas para future-import pós-docstring, `__init__.py` vazios ou helpers privados)
- [ ] `python scripts/policy_check.py src tests scripts` → exit 0 (AC-01/02)
- [ ] Full gate verde com o baseline commitado
- [ ] Test count: suíte completa passa (sem regressão nos 762+ testes existentes)

**Tests**: integration (verificação via execução do CLI sobre a árvore real)
**Gate**: full
**Commit**: `chore(policies): popula baseline de débito pré-existente (AD-005 + carve-out de print)`

---

### T13: Config do pre-commit + documentação de uso [P]

**What**: `.pre-commit-config.yaml` com `repo: local`, `language: system` — hooks `black`, `ruff`, `medasist-policy`, `pytest` (policy/pytest com `pass_filenames: false`); nota de fallback `python -m pre-commit run --all-files` e gotcha do `.env` (AGENTS.md ou README).
**Where**: `.pre-commit-config.yaml`, `AGENTS.md` (seção curta), `README.md` (opcional)
**Depends on**: T11, T12
**Reuses**: OQ-04 (design.md)
**Requirement**: REQ-POL-04, REQ-POL-16

**Tools**:
- MCP: NONE
- Skill: NONE

**Done when**:
- [ ] YAML válido (`pre-commit validate-config`)
- [ ] `pre-commit install` (Windows/pwsh) + `pre-commit run --all-files` passa em árvore limpa (validação manual local — não roda em CI)
- [ ] Commit com violação de policy é bloqueado com a mensagem do hook (AC-16) — validação manual
- [ ] Fallback e gotcha do `.env` documentados

**Tests**: none (config; validação manual + build gate)
**Gate**: build (manual local)
**Commit**: `chore(policies): adiciona hooks locais de pre-commit`

---

### T14: Etapa de policy no CI [P]

**What**: Adicionar step "Policy check" entre "Lint (ruff check)" e "Testes" em `ci.yml`: `python scripts/policy_check.py src tests scripts`. **Não tocar `deploy.yml`** (AC-21).
**Where**: `.github/workflows/ci.yml`
**Depends on**: T12
**Reuses**: Estrutura atual do job `test` (actions/checkout@v4, setup-python, requirements.lock)
**Requirement**: REQ-POL-05, REQ-POL-17, REQ-POL-21

**Tools**:
- MCP: NONE
- Skill: NONE

**Done when**:
- [ ] YAML válido; step posicionado entre ruff e pytest
- [ ] `deploy.yml` sem diff na branch (AC-21)
- [ ] Simulação: push de branch com violação → job vermelho na policy; corrigido → verde (validação manual após push do PR)

**Tests**: none (config; validação via CI real no PR)
**Gate**: build
**Commit**: `ci: adiciona etapa de policy check entre lint e testes`

---

### T15: PR template com checklist [P]

**What**: `.github/pull_request_template.md` com checklist de convenções AGENTS.md (future import, pathlib, logger, docstrings, 50/4/800), black/ruff, testes + cobertura, policy/baseline (atualizar `policies.toml` se aplicável), regra de dado de paciente.
**Where**: `.github/pull_request_template.md`
**Depends on**: None
**Reuses**: GitHub auto-detection de `pull_request_template.md`
**Requirement**: REQ-POL-22

**Tools**:
- MCP: NONE
- Skill: NONE

**Done when**:
- [ ] Arquivo criado em `.github/pull_request_template.md`
- [ ] Checklist cobre AGENTS.md + black/ruff + testes + policy/baseline (AC-22)
- [ ] Validação manual: PR novo na branch exibe o template

**Tests**: none (config; validação manual)
**Gate**: build
**Commit**: `docs: adiciona pull request template com checklist de convenções`

---

### T16: pre-commit no requirements-dev + lock [P]

**What**: Adicionar `pre-commit==4.x` (versão atual 4.x no momento da implementação) ao `requirements-dev.txt` e regenerar `requirements.lock` via `pip-compile --output-file=requirements.lock --strip-extras requirements-dev.txt requirements.txt`.
**Where**: `requirements-dev.txt`, `requirements.lock`
**Depends on**: None
**Reuses**: OQ-04; lock existente gerado por pip-tools (cabeçalho do arquivo)
**Requirement**: REQ-POL-04

**Tools**:
- MCP: NONE
- Skill: build-with-tests

**Done when**:
- [ ] `requirements-dev.txt` lista `pre-commit` pinado (4.x)
- [ ] `requirements.lock` regenerado com pre-commit + transitivas (cfgv, identify, nodeenv, pyyaml, virtualenv) — diff apenas dessas linhas
- [ ] Full gate: instalação do lock + `pytest` verde no CI/venv
- [ ] Test count: suíte completa passa com o novo lock

**Tests**: none (dependências; validação via full gate)
**Gate**: full
**Commit**: `chore(deps): adiciona pre-commit ao requirements-dev e regenera lock`

---

### T17: Target `policy` no Makefile [P]

**What**: Target `policy` rodando `python scripts/policy_check.py src tests scripts` (+ `.PHONY`).
**Where**: `Makefile`
**Depends on**: T12
**Reuses**: Estrutura de targets de qualidade existente (`test`, `lint`, `format`)
**Requirement**: REQ-POL-01, REQ-POL-06

**Tools**:
- MCP: NONE
- Skill: NONE

**Done when**:
- [ ] `make policy` exit 0 na árvore com baseline; exit 1 com violação injetada
- [ ] Não altera `.req-hash`/targets existentes

**Tests**: none (config; validação manual)
**Gate**: build
**Commit**: `chore(policies): adiciona target make policy`

---

### T18: Suíte de aceite do policy-as-code

**What**: `tests/acceptance/test_policy_as_code.py` cobrindo os ACs executáveis por pytest: exit 0 árvore conforme (AC-01/02), exit 1 + relato (AC-06), future import (AC-03/07/20), pathlib (AC-08), print/carve-out/capsys (AC-09/18), logger (AC-10), docstring (AC-11/20), 50/4/800 inclusivos (AC-12/19), paciente (AC-13), baseline nova/obsoleta (AC-14/15). ACs de pre-commit/CI/template (AC-04/05/16/17/21/22) validados manualmente + code review (anotados no arquivo).
**Where**: `tests/acceptance/test_policy_as_code.py`
**Depends on**: T11, T12, T13, T14, T15, T16, T17
**Reuses**: Padrão `tests/acceptance/` existente; fixtures em `tmp_path` (TESTING.md Parallelism Assessment)
**Requirement**: REQ-POL-01, 02, 03, 06, 07, 08, 09, 10, 11, 12, 13, 14, 15, 18, 19, 20

**Tools**:
- MCP: NONE
- Skill: build-with-tests

**Done when**:
- [ ] Cada AC executável tem teste com cenário WHEN/THEN (fixtures `tmp_path` com árvore sintética e baseline sintética)
- [ ] Contrato `capsys` de `tests/scripts/test_evaluate_rag.py` segue passando (AC-18)
- [ ] Full gate verde: `black src/ tests/ scripts/ && ruff check src/ tests/ scripts/ && pytest tests/ -v --cov=src --cov-fail-under=80`
- [ ] Test count: suíte completa passa (sem deleção silenciosa)

**Tests**: integration (acceptance)
**Gate**: full
**Commit**: `test(policies): adiciona suíte de aceite do policy-as-code`

---

## Parallel Execution Map

```
Phase 1 (Sequential):
  T1 ──→ T2

Phase 2 (Parallel):
  T2 complete, then:
    ├── T3 [P]
    ├── T4 [P]
    ├── T5 [P]
    ├── T6 [P]
    ├── T7 [P]
    ├── T8 [P]
    └── T9 [P]
  Independent (no deps, may start any time):
    ├── T15 [P]
    └── T16 [P]

Phase 3 (Sequential):
  T10 ──→ T11 ──→ T12

Phase 4 (Parallel):
  T12 complete, then:
    ├── T13 [P]
    ├── T14 [P]
    └── T17 [P]
  Independent (already started in Phase 2):
    ├── T15 [P]
    └── T16 [P]

Phase 5 (Sequential):
  T18
```

**Parallelism constraint:** T3–T9, T13, T14, T15, T16, T17 satisfazem: sem dependências não concluídas; testes unit/script são parallel-safe (TESTING.md Parallelism Assessment — mocks, `tmp_path`, sem estado global); sem estado mutável compartilhado entre tasks `[P]` da mesma fase (cada uma cria arquivos próprios; `rules.py` é o único arquivo compartilhado entre T3–T9 — resolver: cada task adiciona a sua função ao mesmo `rules.py`; conflito de escrita evitado porque sub-agentes rodam em fases distintas **apenas para edição do mesmo arquivo** → **não rodar T3–T9 em paralelo real se o orchestrator delegar escrita concorrente no mesmo arquivo; alternativa: permitir paralelo com merge sequential dos diffs no final da fase**). **Decisão de execução:** T3–T9 podem ser planejados em paralelo, mas a escrita concorrente em `rules.py` exige ou (a) um único agente para o arquivo com testes por arquivo separado, ou (b) escrita sequencial rápida. Recomendado: executar T3–T9 com sub-agentes que devolvem patches — o orchestrator aplica em sequência; a fase é "parallel-plannable", não "parallel-write".

## Task Granularity Check

| Task | Scope | Status |
|------|-------|--------|
| T1: report.py + __init__ + test | 2 arquivos de código + 1 teste (coeso) | ✅ Granular |
| T2: baseline.py + test | 1 módulo + 1 teste | ✅ Granular |
| T3–T9: regras individuais + testes | 1 função por task + 1 teste | ✅ Granular |
| T10: scanner.py + test | 1 módulo + 1 teste | ✅ Granular |
| T11: CLI + teste de script | 1 script + 1 teste | ✅ Granular |
| T12: baseline population | 1 arquivo (policies.toml) | ✅ Granular |
| T13: pre-commit config + docs | 1 config + docs curtas (coeso) | ✅ Granular |
| T14: ci.yml step | 1 arquivo config | ✅ Granular |
| T15: PR template | 1 arquivo | ✅ Granular |
| T16: requirements + lock | 2 arquivos de deps (coeso) | ✅ Granular |
| T17: Makefile target | 1 arquivo | ✅ Granular |
| T18: acceptance suite | 1 arquivo de teste (integração final) | ✅ Granular |

## Diagram-Definition Cross-Check

| Task | Depends On (task body) | Diagram Shows | Status |
|------|------------------------|---------------|--------|
| T1 | None | Phase 1 start | ✅ Match |
| T2 | T1 | T1 → T2 | ✅ Match |
| T3 | T1 | T1 → T3 (via Phase 2 root) | ✅ Match |
| T4 | T1 | T1 → T4 | ✅ Match |
| T5 | T1, T2 | T1/T2 → T5 | ✅ Match |
| T6 | T1 | T1 → T6 | ✅ Match |
| T7 | T1 | T1 → T7 | ✅ Match |
| T8 | T1 | T1 → T8 | ✅ Match |
| T9 | T1, T2 | T1/T2 → T9 | ✅ Match |
| T10 | T2–T9 | Phase 2 → T10 | ✅ Match |
| T11 | T10 | T10 → T11 | ✅ Match |
| T12 | T11 | T11 → T12 | ✅ Match |
| T13 | T11, T12 | T12 → T13 | ✅ Match |
| T14 | T12 | T12 → T14 | ✅ Match |
| T15 | None | Independent arrow | ✅ Match |
| T16 | None | Independent arrow | ✅ Match |
| T17 | T12 | T12 → T17 | ✅ Match |
| T18 | T11–T17 | Phase 4 → T18 | ✅ Match |

## Test Co-location Validation

| Task | Code Layer Created/Modified | Matrix Requires | Task Says | Status |
|------|----------------------------|-----------------|-----------|--------|
| T1 | `policies/report.py` (novo) | unit (novo row: `tests/policies/`) | unit | ✅ OK |
| T2 | `policies/baseline.py` (novo) | unit | unit | ✅ OK |
| T3 | `policies/rules.py` (novo) | unit | unit | ✅ OK |
| T4 | `policies/rules.py` | unit | unit | ✅ OK |
| T5 | `policies/rules.py` | unit | unit | ✅ OK |
| T6 | `policies/rules.py` | unit | unit | ✅ OK |
| T7 | `policies/rules.py` | unit | unit | ✅ OK |
| T8 | `policies/rules.py` | unit | unit | ✅ OK |
| T9 | `policies/rules.py` | unit | unit | ✅ OK |
| T10 | `policies/scanner.py` (novo) | unit | unit | ✅ OK |
| T11 | `scripts/policy_check.py` (novo) | unit (row scripts) | unit | ✅ OK |
| T12 | `policies.toml` (dados/config) | none | integration (verificação CLI) | ✅ OK |
| T13 | `.pre-commit-config.yaml` (config) | none | none (build manual) | ✅ OK |
| T14 | `ci.yml` (config) | none | none (build) | ✅ OK |
| T15 | PR template (docs) | none | none | ✅ OK |
| T16 | requirements (deps) | none | none (full gate) | ✅ OK |
| T17 | Makefile (config) | none | none | ✅ OK |
| T18 | `tests/acceptance/` (aceite) | integration | integration | ✅ OK |

**Nota TESTING.md:** a matriz existente não tem linha para `policies/*`; T1–T10 criam a linha nova `policies/* → unit → tests/policies/` e `scripts/policy_check.py → unit → tests/scripts/test_policy_check.py`, alinhadas ao padrão espelhado `src/medasist/` → `tests/`.

---

## Tips de execução (para o builder)

1. **Regras puras primeiro** — cada `check_*` não faz IO; testes não precisam de fixtures de arquivo real (usam strings com `\n` e `\r\n`).
2. **CRLF** — sempre `text.splitlines()` para contagem; nunca `text.count("\n")` sozinho.
3. **`__init__.py` do pacote policies** — docstring + `from __future__ import annotations`, sem `__all__` (padrão `monitoring/`), para a própria feature não se auto-violar.
4. **CLI sem `print()`** — relatório via `sys.stdout.write`; erros fatais via `logger.error`.
5. **Baseline population (T12)** — rodar o gerador, conferir as 8 entradas esperadas, commitar; nunca editar `policies.toml` à mão fora do gerador.
6. **CI (T14)** — manter `deploy.yml` intocado; validar com YAML parse e, após push do PR, confirmar o step verde/vermelho.
7. **Code review obrigatório** — antes do PR, rodar a skill `code-reviewer` (AGENTS.md).