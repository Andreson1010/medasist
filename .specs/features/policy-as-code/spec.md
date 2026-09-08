# Validação Automática de Convenções e Regras de Segurança (Policy-as-Code) — Technical Spec

**Path:** `.specs/features/policy-as-code/spec.md`
**TLC scope:** large
**Based on story:** Como desenvolvedor(a) do MedAssist, quero que as convenções do AGENTS.md e as regras de segurança sejam validadas automaticamente por um policy checker em Python — local via pre-commit e bloqueante no CI em PRs — para que código fora do padrão nunca chegue à main e não dependa de revisão manual.
**Status:** Awaiting human approval

---

## Problem Statement

As convenções obrigatórias do AGENTS.md (future import, pathlib, logger, docstrings NumPy, limites 50/4/800, sem `print()`) e a regra de segurança "nenhum dado real de paciente" dependem hoje de revisão manual para serem aplicadas — revisão que falha em ~35 arquivos pré-existentes com débito acumulado. Não existe mecanismo automatizado (local nem CI) que impeça código fora do padrão de chegar à `main`. Esta feature cria um policy checker em Python (stdlib-only) que valida as convenções e regras de segurança, com baseline/allowlist para o débito pré-existente, executado shift-left via pre-commit local e etapa bloqueante no CI.

## Goals

- [ ] Bloquear commits e PRs que violem convenções do AGENTS.md ou regras de segurança, sem depender de revisão manual (AC-04, AC-16, AC-17).
- [ ] Árvore atual passa no 1º commit com exit 0, sem corrigir débito pré-existente (baseline/allowlist — AC-02).
- [ ] Baseline não mascara violação nova (AC-14) e exige limpeza de entrada obsoleta (AC-15).

## Out of Scope

| Feature | Reason |
|---------|--------|
| Corrigir as 7 funções >50 linhas (AD-005) | OQ-01: débito pré-existente entra no baseline; correção incremental depois, fora desta feature |
| Corrigir os ~10 `from __future__` após docstring e ~17 `__init__.py` vazios | OQ-02: com a regra "primeira linha executável não-docstring", são legais e isentos — sem baseline |
| Refatorar `print()` do `scripts/evaluate_rag.py` | OQ-03: carve-out por allowlist explícita (arquivo+símbolo); comportamento `capsys` preservado |
| Branch protection no GitHub | Manual, fora do escopo de código (follow-up) |
| Alterar `deploy.yml` / zero-downtime / políticas de deploy | AC-21: deploy já gateia por CI success; fica inalterado |
| `.env` / segredos / validação de secrets | Não é convenção de código validável por scanner estático |
| Regras de runtime (disclaimer, cold start, citação) | Já testadas na suíte (TESTING.md "Safety Rule Testing"); fora do escopo do scanner estático |
| mypy / novas regras de lint (ruff) | AD-001 fixou o conjunto E/W/F/I/B/UP/C4/SIM; mudar config de lint é outra feature |
| Documentação em `docs/` (gitignored) e `evals/` | Fora da varredura padrão (OQ-08) |

---

## Emendas aprovadas (Checkpoint 2.5)

Emendas de escopo aprovadas pelo humano após revisão do validator (IMP-01,
IMP-02, IMP-03). **Válidas e vinculantes** — o checker implementa exatamente
este comportamento:

- **(a) DOCSTRING (AC-11) e COMPLEXITY 50/4/800 (AC-12/AC-19) escopadas a `src/`**
  — as convenções de docstring e limites físicos são convenções de código-fonte.
  Arquivos de `tests/` e `scripts/` (funções de teste sem docstring, arquivos de
  teste >800 linhas) ficam fora da regra; baseliná-los seria impraticável e
  contradiz o plano de baseline do débito pré-existente.
- **(b) LOGGER (AC-10) isenta todo `__init__.py`** — `__init__.py` são marcadores
  de pacote/pontos de re-export (ex.: `src/medasist/evaluation/__init__.py`),
  não módulos de código; a isenção vale mesmo para `__init__.py` não-vazios.
- **(c) PATHLIB (AC-08) restrita a sinais inequívocos** — flagga apenas: primeiro
  argumento do builtin `open(...)`, prefixo de drive Windows (`C:\...`) ou
  atribuição a variável de nome path-named não-privada (path/file/dir/output)
  com separador ou extensão conhecida. Strings que apenas "terminam com
  extensão" ou "contêm `/`" em outros contextos (fixtures `source="bula.pdf"`,
  `"5/minute"`, `"BAAI/bge-reranker-base"`, `"text/event-stream"`) não são
  flaggadas.

**Nota de cobertura dos ACs:** AC-08, AC-10, AC-11, AC-12 (e AC-13, cujo
allowlist de tokens sintéticos e escape-hatch por baseline são o mecanismo
documentado para fixtures) são implementados e testados conforme estas emendas;
a "árvore passa no 1º commit" (AC-01/02) depende delas.

---

## User Stories

### P1: Policy checker CLI com contrato de exit ⭐ MVP

**User Story**: Como dev, quero um CLI (`scripts/policy_check.py`) que varre `src/ tests/ scripts/` e reporta violações (regra, arquivo, local) com exit 0/1, para saber de forma determinística se a árvore está em conformidade.

**Why P1**: É a fundação — toda automação (pre-commit, CI) depende do contrato de exit.

**Acceptance Criteria**:
1. WHEN o repositório está em conformidade (sem violações novas e com baseline válido) THEN o CLI SHALL sair com exit 0 (AC-01).
2. WHEN existe ≥1 violação não baselinada THEN o CLI SHALL sair com exit 1 e relatar cada violação com regra, arquivo e local (linha/símbolo) (AC-06).

**Independent Test**: Rodar `python scripts/policy_check.py src tests scripts` na árvore com baseline populado → exit 0; inserir um `print("x")` em um arquivo de `src/` → exit 1 com relato.

---

### P1: Regras estruturais das convenções do AGENTS.md ⭐ MVP

**User Story**: Como dev, quero que as convenções obrigatórias sejam regras estáticas validadas por arquivo, para que código fora do padrão seja pego na hora.

**Why P1**: É o núcleo do valor — codifica as convenções hoje só documentadas.

**Acceptance Criteria**:
1. WHEN um arquivo tem `from __future__ import annotations` como primeira linha executável (docstring de módulo pode preceder) THEN o CLI SHALL não reportar violação (AC-03).
2. WHEN um arquivo não tem `from __future__ import annotations` OU tem código executável antes dele THEN o CLI SHALL reportar violação (AC-07).
3. WHEN um path é usado como string bruta onde pathlib é a convenção (ex.: `open("...")`, string com separador de path ou extensão de arquivo fora de `Path(...)`/`str(...)`) THEN o CLI SHALL reportar violação (AC-08).
4. WHEN existe chamada `print()` fora do carve-out de CLI (allowlist arquivo+símbolo) THEN o CLI SHALL reportar violação (AC-09).
5. WHEN um módulo em `src/` com conteúdo executável não declara `logger = logging.getLogger(__name__)` THEN o CLI SHALL reportar violação (AC-10).
6. WHEN uma função/classe pública (top-level ou método público, não `_`-prefixed, não dunder) não tem docstring THEN o CLI SHALL reportar violação (AC-11).
7. WHEN função tem >50 linhas, OU aninhamento >4, OU arquivo >800 linhas (fora do baseline) THEN o CLI SHALL reportar violação (AC-12).
8. WHEN função tem exatamente 50 linhas, OU aninhamento exatamente 4, OU arquivo exatamente 800 linhas THEN o CLI SHALL NÃO reportar violação (limites inclusivos) (AC-19).
9. WHEN o arquivo é vazio, OU é `__init__.py` sem conteúdo executável, OU o símbolo é privado (`_`-prefixed) ou dunder THEN o CLI SHALL isentá-lo das regras de future import e docstring (AC-20).

**Independent Test**: Testes unitários por regra com amostras sintéticas positivas/negativas e casos-limite 50/51, 4/5, 800/801.

---

### P1: Regra de segurança — dado real de paciente ⭐ MVP

**User Story**: Como dev, quero que padrões configurados de dado real de paciente (CPF, RG, cartão SUS, telefone/WhatsApp, e-mail, data de nascimento) em código/testes/logs sejam violação, com allowlist para fixtures sintéticas.

**Why P1**: É uma das 4 regras inegociáveis do AGENTS.md e hoje só depende de revisão manual.

**Acceptance Criteria**:
1. WHEN um texto contém padrão configurado de dado real de paciente (fora da allowlist) THEN o CLI SHALL reportar violação (AC-13).

**Independent Test**: Arquivo sintético com CPF/telefone/e-mail/DOB → exit 1 com relato por linha; arquivo com nomes sintéticos (Zolatril, Alphazol, Betazol, amoxicilina, ibuprofeno, omeprazol, etc.) → sem violação.

---

### P1: Baseline/allowlist do débito pré-existente ⭐ MVP

**User Story**: Como dev, quero um baseline em `policies.toml` que cubra as violações pré-existentes (allowlist), para que a árvore atual passe no 1º commit sem precisar corrigir o débito nesta feature — e que a baseline não mascare regressões.

**Why P1**: Sem baseline, o 1º commit falharia em ~9 entradas (7 funções >50 linhas + carve-out de print) — o gate nunca seria adotado.

**Acceptance Criteria**:
1. WHEN a baseline está presente e cobre as violações pré-existentes THEN o CLI SHALL sair com exit 0 na árvore atual (AC-02).
2. WHEN a baseline está presente e existe violação nova (não baselinada) THEN o CLI SHALL sair com exit 1 (AC-14).
3. WHEN a baseline tem entrada obsoleta (violação foi corrigida/removida) THEN o CLI SHALL sair com exit 1 e exigir limpeza da entrada (AC-15).
4. WHEN `print()` intencional de stdout de CLI em `scripts/` está na allowlist (arquivo+símbolo, ex.: `scripts/evaluate_rag.py` + `_print_report`) THEN o CLI SHALL não reportar violação e o contrato `capsys` dos testes existentes SHALL ser preservado (AC-18).

**Independent Test**: Rodar na árvore com baseline populado → exit 0; adicionar `print()` novo em `src/` → exit 1; remover um print allowlistado do `evaluate_rag.py` → exit 1 exigindo limpeza.

---

### P1: Pre-commit local (Windows/pwsh) ⭐ MVP

**User Story**: Como dev, quero hooks pre-commit locais (black, ruff, policy, pytest) para que todo commit em árvore não conformante seja bloqueado com a mensagem do hook.

**Why P1**: É o shift-left local — pega a violação antes do push.

**Acceptance Criteria**:
1. WHEN pre-commit está instalado (Windows/pwsh) com hooks black/ruff/policy/pytest e a árvore está limpa THEN o commit SHALL passar (AC-04).
2. WHEN um commit viola qualquer hook THEN o commit SHALL ser bloqueado com a mensagem do hook (AC-16).

**Independent Test**: `pre-commit install` + commit em árvore limpa → passa; inserir violação → commit bloqueado com saída do hook correspondente.

---

### P1: CI bloqueante em PRs (com deploy inalterado) ⭐ MVP

**User Story**: Como dev, quero uma etapa de policy no CI entre lint e testes para que PR com violação falhe de forma bloqueante, sem alterar o deploy.

**Why P1**: É o gate final — garante que nada fora do padrão chegue à main mesmo com pre-commit pulado.

**Acceptance Criteria**:
1. WHEN o código está em conformidade THEN a etapa de policy no CI SHALL passar e o job SHALL ficar verde (AC-05).
2. WHEN um PR contém violação THEN o CI SHALL falhar na etapa de policy (status bloqueante) (AC-17).
3. WHEN o CI falha THEN o deploy SHALL continuar bloqueado por CI success, com `deploy.yml` inalterado (AC-21).

**Independent Test**: Push de branch com violação → `ci.yml` vermelho na etapa "Policy check"; corrigir → verde; `deploy.yml` sem diff.

---

### P1: PR template com checklist de convenções ⭐ MVP

**User Story**: Como dev, quero que todo PR abra com um checklist de convenções (AGENTS.md + black/ruff/testes/policy/baseline) para guiar a revisão e a autoverificação.

**Why P1**: Barato, auto-aplicado pelo GitHub, e documenta o fluxo de manutenção da baseline.

**Acceptance Criteria**:
1. WHEN um PR é aberto THEN o GitHub SHALL auto-aplicar `.github/pull_request_template.md` com checklist cobrindo convenções AGENTS.md, black/ruff, testes e policy/baseline (AC-22).

**Independent Test**: Abrir um PR na branch e verificar que o template aparece preenchido no corpo.

---

## Edge Cases

- WHEN o arquivo está vazio ou só tem comentários/whitespace THEN o CLI SHALL pular todas as regras estruturais (AC-20).
- WHEN o arquivo é `__init__.py` sem conteúdo executável THEN o CLI SHALL isentá-lo de future import e docstring (AC-20).
- WHEN o módulo tem apenas docstring (ex.: `src/medasist/monitoring/__init__.py`) THEN future import na posição permitida (após docstring) SHALL ser legal (OQ-02).
- WHEN um arquivo usa CRLF (Windows) THEN a contagem de linhas para limites 50/4/800 SHALL normalizar `\r\n` antes de contar (Windows/pwsh).
- WHEN o caminho usa `\` (Windows) THEN a chave de baseline SHALL normalizar para `/` (POSIX) para matching estável entre SOs.
- WHEN um path extra é passado (`--path`) que inclui `.opencode/` ou `.agents/` THEN as exclusões obrigatórias SHALL continuar valendo (OQ-08).
- WHEN a baseline não existe e há violações THEN o CLI SHALL exit 1 listando tudo (nenhuma violação é mascarada por ausência de baseline).
- WHEN a baseline tem entrada inativa (`active=false`) THEN ela SHALL ser ignorada (não suprime violação, não é obsoleta).
- WHEN uma violação é corrigida mas a entrada de baseline permanece THEN o CLI SHALL exit 1 (AC-15) com a lista de entradas obsoletas a remover.
- WHEN o arquivo não existe mais (foi deletado) e tem entrada de baseline THEN a entrada SHALL ser considerada obsoleta (AC-15).
- WHEN `print()` existe em símbolo não allowlistado dentro de `scripts/` THEN é violação (o carve-out é por arquivo+símbolo, não por diretório inteiro).
- WHEN o próprio `scripts/policy_check.py` precisa emitir relatório em stdout THEN o relatório SHALL usar `sys.stdout.write` (a regra NO-PRINT flagga apenas chamadas `print()`, evitando autorreferência).

---

## Requirement Traceability

| Requirement ID | Story | Phase | Status |
|----------------|-------|-------|--------|
| REQ-POL-01 (AC-01) | P1: CLI exit 0 | Design | Pending |
| REQ-POL-02 (AC-02) | P1: Baseline cobre pré-existentes | Design | Pending |
| REQ-POL-03 (AC-03) | P1: future import posição permitida | Design | Pending |
| REQ-POL-04 (AC-04) | P1: pre-commit Windows/pwsh hooks | Design | Pending |
| REQ-POL-05 (AC-05) | P1: CI policy step verde | Design | Pending |
| REQ-POL-06 (AC-06) | P1: exit 1 + relato | Design | Pending |
| REQ-POL-07 (AC-07) | P1: future import ausente/atrasado | Design | Pending |
| REQ-POL-08 (AC-08) | P1: path string bruta | Design | Pending |
| REQ-POL-09 (AC-09) | P1: print() fora do carve-out | Design | Pending |
| REQ-POL-10 (AC-10) | P1: logger ausente em src/ | Design | Pending |
| REQ-POL-11 (AC-11) | P1: docstring NumPy ausente | Design | Pending |
| REQ-POL-12 (AC-12) | P1: limites 50/4/800 | Design | Pending |
| REQ-POL-13 (AC-13) | P1: dado real de paciente | Design | Pending |
| REQ-POL-14 (AC-14) | P1: baseline + violação nova → exit 1 | Design | Pending |
| REQ-POL-15 (AC-15) | P1: baseline obsoleta → exit 1 | Design | Pending |
| REQ-POL-16 (AC-16) | P1: commit bloqueado pelo hook | Design | Pending |
| REQ-POL-17 (AC-17) | P1: PR com violação → CI falha | Design | Pending |
| REQ-POL-18 (AC-18) | P1: carve-out stdout CLI + capsys | Design | Pending |
| REQ-POL-19 (AC-19) | P1: limites inclusivos | Design | Pending |
| REQ-POL-20 (AC-20) | P1: isenções vazio/__init__/privados/dunders | Design | Pending |
| REQ-POL-21 (AC-21) | P1: deploy.yml inalterado | Design | Pending |
| REQ-POL-22 (AC-22) | P1: PR template auto-aplicado | Design | Pending |

**ID format:** `REQ-POL-[NUMBER]` — prefixo POL = policy-as-code.
**Status values:** Pending → In Design → In Tasks → Implementing → Verified
**Coverage:** 22 total, 22 mapeadas em tasks (ver tasks.md), 0 não mapeadas

---

## Data Model Changes

Novas dataclasses congeladas (padrão do repositório — `@dataclass(frozen=True)`) em `src/medasist/policies/`:

- **`PolicyViolation`** — `rule_id: str`, `path: str` (relativo à raiz, separadores POSIX), `line: int`, `message: str`, `baseline: bool` (True se suprimida por baseline). Campo `symbol: str | None = None` para localização simbólica quando aplicável.
- **`PolicyReport`** — `violations: tuple[PolicyViolation, ...]`, `baseline_entries: tuple[BaselineEntry, ...]`, `obsolete_entries: tuple[BaselineEntry, ...]`, `files_scanned: int`, `files_skipped: int`; propriedades `total_violations`, `new_violations`, `baselined_violations`.
- **`BaselineEntry`** — `rule_id: str`, `path: str`, `location: str` (símbolo para regras de função — FUNC-LENGTH/NESTING/NO-PRINT; linha para PATIENT-DATA/PATHLIB; `"<file>"` para FILE-LENGTH), `reason: str`, `active: bool = True`.

Armazenamento: **`policies.toml` na raiz do repositório** (decisão de design — ver `design.md`), lido com `tomllib` (stdlib Python 3.11). Nenhuma migração de banco de dados.

## Process / Background Flow

**Happy path (scan):** CLI → scanner coleta `.py` em `src/ tests/ scripts/` (exclui `.opencode/`, `.agents/`, `.git/`, `data/`, `evals/`, `chroma_db/`, `logs/`, `docs/`, `node_modules/`, `*.lock`, `__pycache__`, ocultos) → para cada arquivo roda cada rule-checker (função pura `arquivo → list[PolicyViolation]`) → baseline suprime violações cobertas (match por `rule_id+path+location`) → entradas de baseline sem violação correspondente viram `obsolete` → relatório em stdout via `sys.stdout.write` → exit 0 se 0 violações novas e 0 obsoletas; senão exit 1.

**Failure path — violação nova:** scanner reporta regra/arquivo/local, exit 1 (AC-06/14).

**Failure path — baseline obsoleta:** relatório lista entradas a limpar, exit 1 (AC-15).

**Failure path — baseline ausente com violações:** tudo listado como violação nova, exit 1.

**Happy path (pre-commit):** `pre-commit run` → hooks locais black → ruff → policy → pytest; falha de qualquer hook bloqueia o commit (AC-16).

**Happy path (CI):** `ci.yml` → black --check → ruff → **policy step** (`python scripts/policy_check.py src tests scripts`) → pytest; falha da policy falha o job (AC-17); `deploy.yml` dispara apenas em CI success (AC-21).

## API Changes

No API changes.

## Frontend Changes

No frontend changes.

## Tests Required

**Unit (novos, em `tests/policies/`)** — espelhando `src/medasist/policies/`:
- `test_report.py` — frozen dataclasses, contadores, baseline flags.
- `test_baseline.py` — load/save TOML, match por `rule_id+path+location`, detecção de obsoleta, entrada inativa, path normalizado POSIX, arquivo inexistente.
- `test_rules_future_import.py` — docstring antes, comentários, vazio, `__init__.py` sem conteúdo, ausente, código executável antes, arquivo só-docstring.
- `test_rules_pathlib.py` — `open("...")`, string com separador/extensão, `Path(...)` ok, `str(...)` ok, docstring ignorada, URL ignorada.
- `test_rules_no_print.py` — print em src/tests/scripts fora da allowlist, allowlist por arquivo+símbolo, `sys.stdout.write` ok.
- `test_rules_logger.py` — módulo com/sem logger, `__init__.py` vazio isento, logger com nome diferente.
- `test_rules_docstring.py` — pública sem docstring, privada isenta, dunder isento, método público de classe, `__init__.py` vazio isento.
- `test_rules_complexity.py` — limites inclusivos 50/51, 4/5, 800/801; CRLF; aninhamento.
- `test_rules_patient_data.py` — CPF/RG/SUS/telefone/e-mail/DOB positivos; allowlist sintéticos (Zolatril, Alphazol, Betazol, amoxicilina, ibuprofeno, omeprazol, etc.) negativos; datas fora do range etário.
- `test_scanner.py` — traversal, exclusões obrigatórias, `--path`/`--exclude` extras, exclusão de não-`.py`.

**Script tests (novos, `tests/scripts/test_policy_check.py`)** — padrão `from policy_check import ...` (via `pythonpath=["src","scripts"]`), `mocker.patch`, argv lists, assert exit 0/1, `capsys` no relatório.

**Aceite (novos, `tests/acceptance/test_policy_as_code.py`)** — AC-01, 02, 06, 07, 08, 09, 10, 11, 12, 13, 14, 15, 18, 19, 20 via execução do CLI sobre fixtures em `tmp_path`; AC-04/16 (pre-commit) e AC-05/17 (CI) validados manualmente + code review (pre-commit/CI não rodam em pytest).

**Cobertura**: o módulo novo `src/medasist/policies/*` nasce com testes no mesmo PR (gate `--cov-fail-under=80` com `source=["src"]`).

**Existing tests that must keep passing**: `tests/scripts/test_evaluate_rag.py` (contrato `capsys` do `_print_report` — AC-18), `tests/acceptance/*`, suíte completa (762+ testes).

## Files That Will Change

| File | Change type | Why |
|------|-------------|-----|
| `src/medasist/policies/__init__.py` | New | Pacote `policies` (re-exports + `__all__`) |
| `src/medasist/policies/report.py` | New | `PolicyViolation`, `PolicyReport` (frozen dataclasses) |
| `src/medasist/policies/baseline.py` | New | `BaselineEntry`, load/save `policies.toml`, match, detecção de obsoleta |
| `src/medasist/policies/rules.py` | New | Rule-checkers puros (future-import, pathlib, no-print, logger, docstring, complexity, patient-data) |
| `src/medasist/policies/scanner.py` | New | Traversal + exclusões + orquestração das regras |
| `scripts/policy_check.py` | New | CLI argparse thin: `parse_args(argv)`, `main(argv)->int`, `sys.exit(main())` |
| `tests/policies/` (9 arquivos) | New | Testes unitários por regra + report/baseline/scanner |
| `tests/scripts/test_policy_check.py` | New | Testes do CLI (exit 0/1, capsys) |
| `tests/acceptance/test_policy_as_code.py` | New | Aceite dos ACs executáveis |
| `policies.toml` | New | Baseline/allowlist (débito pré-existente + carve-out de print) |
| `.pre-commit-config.yaml` | New | Hooks locais black/ruff/policy/pytest |
| `.github/pull_request_template.md` | New | Checklist de convenções (AC-22) |
| `.github/workflows/ci.yml` | Modified | Step "Policy check" entre ruff e pytest |
| `requirements-dev.txt` | Modified | Adicionar `pre-commit` (4.x) (OQ-04) |
| `requirements.lock` | Modified | Regenerado via `pip-compile` (OQ-04) |
| `Makefile` | Modified (opcional) | Target `policy` |
| `AGENTS.md` | Modified (opcional) | Seção curta de uso do pre-commit + gotcha do `.env` |
| `README.md` | Modified (opcional) | Comandos do policy checker |
| `deploy.yml` | **Sem mudança** | AC-21 — deploy continua bloqueado por CI success |

## Risks

1. **Interação com gate de cobertura** — `--cov-fail-under=80` com `source=["src"]` mede o módulo novo; se `policies/*` nascer sem testes no mesmo PR, o CI falha. Mitigação: tasks exigem testes co-localizados (TESTING.md), T1–T10 incluem testes no mesmo commit.
2. **Falso-positivo do regex de paciente** — padrões de data/telefone/CPF podem casar números de fixtures (datas em docstrings, IDs numéricos, versões). Mitigação: padrões ancorados (`\b`), range etário para DOB, allowlist de tokens sintéticos (Zolatril, Alphazol, etc.), baseline como escape hatch, testes com fixtures sintéticas.
3. **Custo de manutenção da baseline** — pagar débito incrementalmente exige remover entradas ao corrigir; esquecer gera exit 1 (AC-15) que trava o commit até limpar. Mitigação: `policies.toml` dedicado (churn não polui `pyproject.toml`), gerador `--baseline-generate`, checklist no PR template, relatório claro de obsoletas.
4. **Windows/CRLF na contagem de linhas** — separador `\r\n` e drive letters (`C:\...`) quebram contagem e normalização. Mitigação: leitura com `newline=""` + `splitlines()`, chaves de baseline normalizadas para POSIX.
5. **Drift de versão / ambiente do pre-commit** — hooks `language: system` dependem do venv com black/ruff/pytest pinados no PATH; versões driftam se o venv for recriado sem lock. Mitigação: versões pinadas em `requirements-dev.txt` + `requirements.lock` (pip-compile), fallback documentado `python -m pre-commit run --all-files`.
6. **Gotcha do `.env` no hook de pytest** — `.env` local com linhas de comentário com valor quebra o pytest (documentado no CONTEXT.md). Mitigação: documentar no AGENTS.md/README; não alterar o pytest hook para ignorar `.env` (fora do escopo).
7. **`sys.path` do CLI no CI** — `python scripts/policy_check.py` não tem `src` no path por padrão; se depender de instalação do pacote, CI falha. Mitigação: bootstrap de `sys.path` no topo do script (após future import), stdlib-only, zero deps novas no CI.
8. **Regras driftam do AGENTS.md** — se o AGENTS.md mudar, o checker precisa acompanhar. Mitigação: checklist do PR template referencia as duas fontes; regras implementadas como constantes/lista central em `rules.py`.
9. **Autorreferência do CLI na regra NO-PRINT** — o relatório usa `print()` e o próprio checker se auto-flagga. Mitigação: relatório via `sys.stdout.write` (regra flagga apenas chamadas `print()`); teste explícito.
10. **Churn de merge em `requirements.lock`** — regeneração via pip-compile adiciona deps transitivas do pre-commit (cfgv, identify, nodeenv, virtualenv, etc.). Mitigação: task isolada com commit atômico próprio; CI não usa pre-commit (só o venv do dev).

## Open Questions

None. (OQ-01..08 aprovados e vinculantes; decisões de design restantes estão resolvidas em `design.md`.)

---

## Success Criteria

- [ ] `python scripts/policy_check.py src tests scripts` → exit 0 na árvore atual com baseline populado (AC-01/02).
- [ ] Commit em árvore limpa passa nos 4 hooks pre-commit em Windows/pwsh (AC-04).
- [ ] PR com violação falha no CI na etapa de policy; PR conforme fica verde (AC-05/17).
- [ ] Violação nova introduzida → exit 1 local e CI vermelho; entrada de baseline obsoleta → exit 1 (AC-14/15).
- [ ] `deploy.yml` sem diff na branch (AC-21).
- [ ] Todo PR novo abre com o checklist do `pull_request_template.md` (AC-22).
- [ ] Suíte completa verde: `pytest tests/ -v --cov=src --cov-fail-under=80` com `black`/`ruff` limpos (sem regressão nos testes existentes, incluindo `capsys` do `_print_report`).