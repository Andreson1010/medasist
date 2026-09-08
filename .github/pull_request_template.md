## Descrição

<!-- O que esta PR faz e por quê. -->

## Checklist de convenções (AGENTS.md)

- [ ] Todo arquivo `.py` começa com `from __future__ import annotations` (primeira instrução executável; docstring de módulo pode preceder)
- [ ] Paths usam `pathlib.Path` (nunca strings brutas)
- [ ] Módulos em `src/` declaram `logger = logging.getLogger(__name__)`
- [ ] Funções/classes públicas têm docstring (estilo NumPy, PT-BR)
- [ ] Funções até 50 linhas, aninhamento até 4 níveis, arquivos até 800 linhas
- [ ] Nenhum dado real de paciente em código, testes ou logs (CPF/RG/SUS/telefone/e-mail/DOB); fixtures usam nomes sintéticos

## Qualidade

- [ ] `black --check src/ tests/ scripts/` passa
- [ ] `ruff check src/ tests/ scripts/` passa
- [ ] `pytest tests/ -v --cov=src --cov-fail-under=80` passa
- [ ] `python scripts/policy_check.py src tests scripts` passa (exit 0)

## Baseline (`policies.toml`)

- [ ] `policies.toml` atualizado se novas entradas de débito pré-existente foram necessárias (`python scripts/policy_check.py --baseline-generate`)
- [ ] Entradas obsoletas foram removidas (o policy checker falha com exit 1 se a baseline ficar obsoleta — AC-15)