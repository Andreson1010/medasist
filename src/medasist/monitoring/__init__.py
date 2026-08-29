"""Observabilidade do MedAssist: métricas Prometheus e exposição via HTTP.

Contém ``metrics.py`` com as métricas de negócio e de HTTP, o middleware de
medição e a rota de exposição ``/metrics``.
"""

from __future__ import annotations
