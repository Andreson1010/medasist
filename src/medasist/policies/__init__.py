"""Validação de convenções e regras de segurança do MedAssist (policy-as-code).

Contém os value objects e regras estáticas que codificam as convenções do
AGENTS.md (future import, pathlib, logger, docstrings, limites 50/4/800, sem
``print()``) e a regra de segurança "nenhum dado real de paciente". A baseline
em ``policies.toml`` permite cobrir o débito pré-existente sem mascarar
regressões.
"""

from __future__ import annotations