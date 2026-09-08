"""Value objects do relatório de políticas.

Define ``PolicyViolation`` (uma ocorrência de regra descumprida em um arquivo)
e ``PolicyReport`` (o resultado consolidado de uma varredura), ambos como
dataclasses congeladas seguindo o padrão do repositório.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from medasist.policies.baseline import BaselineEntry

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PolicyViolation:
    """Uma violação de regra de política em um arquivo.

    Attributes
    ----------
    rule_id : str
        Identificador da regra (ex.: ``FUNC-LENGTH``, ``PATIENT-DATA``).
    path : str
        Caminho do arquivo relativo à raiz, com separadores POSIX.
    line : int
        Linha física (1-based) da violação.
    message : str
        Descrição em PT-BR com a evidência encontrada.
    baseline : bool
        ``True`` quando a violação é suprimida por uma entrada de baseline.
    symbol : str | None
        Símbolo (função/classe) associado, quando aplicável.
    """

    rule_id: str
    path: str
    line: int
    message: str
    baseline: bool = False
    symbol: str | None = None


@dataclass(frozen=True)
class PolicyReport:
    """Resultado consolidado de uma varredura de políticas.

    Attributes
    ----------
    violations : tuple[PolicyViolation, ...]
        Todas as violações encontradas (novas e baselinadas).
    baseline_entries : tuple[BaselineEntry, ...]
        Entradas de baseline carregadas de ``policies.toml``.
    obsolete_entries : tuple[BaselineEntry, ...]
        Entradas de baseline sem violação correspondente no scan atual.
    files_scanned : int
        Número de arquivos efetivamente varridos.
    files_skipped : int
        Número de arquivos pulados por erro de leitura.
    """

    violations: tuple[PolicyViolation, ...]
    baseline_entries: tuple[BaselineEntry, ...]
    obsolete_entries: tuple[BaselineEntry, ...]
    files_scanned: int
    files_skipped: int

    @property
    def new_violations(self) -> tuple[PolicyViolation, ...]:
        """Retorna as violações não suprimidas por baseline.

        Returns
        -------
        tuple[PolicyViolation, ...]
            Violações com ``baseline=False``.
        """
        return tuple(v for v in self.violations if not v.baseline)

    @property
    def baselined_violations(self) -> tuple[PolicyViolation, ...]:
        """Retorna as violações suprimidas por baseline.

        Returns
        -------
        tuple[PolicyViolation, ...]
            Violações com ``baseline=True``.
        """
        return tuple(v for v in self.violations if v.baseline)

    @property
    def total_violations(self) -> int:
        """Retorna o número total de violações encontradas.

        Returns
        -------
        int
            Total de violações (novas e baselinadas).
        """
        return len(self.violations)

    @property
    def has_failures(self) -> bool:
        """Indica se a varredura deve falhar o gate (exit 1).

        Returns
        -------
        bool
            ``True`` quando há violações novas ou entradas de baseline
            obsoletas a limpar.
        """
        return bool(self.new_violations) or bool(self.obsolete_entries)
