"""Validação de convenções e regras de segurança do MedAssist (policy-as-code).

Contém os value objects e regras estáticas que codificam as convenções do
AGENTS.md (future import, pathlib, logger, docstrings, limites 50/4/800, sem
``print()``) e a regra de segurança "nenhum dado real de paciente". A baseline
em ``policies.toml`` permite cobrir o débito pré-existente sem mascarar
regressões.
"""

from __future__ import annotations

from medasist.policies.baseline import BaselineEntry, load_baseline, save_baseline
from medasist.policies.report import PolicyReport, PolicyViolation
from medasist.policies.scanner import collect_files, generate_baseline, run_scan

__all__ = [
    "PolicyViolation",
    "PolicyReport",
    "BaselineEntry",
    "collect_files",
    "run_scan",
    "generate_baseline",
    "load_baseline",
    "save_baseline",
]
