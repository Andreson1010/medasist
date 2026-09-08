"""Modelo e IO da baseline/allowlist do policy checker.

A baseline é armazenada em ``policies.toml`` na raiz do repositório: entradas
``[[entries]]`` que cobrem o débito pré-existente (allowlist) e uma seção
``[allowlist.patient_data]`` com tokens sintéticos de fixtures. A leitura usa
``tomllib`` (stdlib); a escrita é um serializador TOML interno mínimo usado
apenas pelo gerador ``--baseline-generate``.
"""

from __future__ import annotations

import logging
import os
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from medasist.policies.report import PolicyViolation

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BaselineEntry:
    """Uma entrada de baseline/allowlist em ``policies.toml``.

    Attributes
    ----------
    rule_id : str
        Identificador da regra (ex.: ``FUNC-LENGTH``, ``NO-PRINT``).
    path : str
        Caminho do arquivo relativo à raiz, com separadores POSIX.
    location : str
        Símbolo para regras de função/print, linha para ``PATIENT-DATA``/
        ``PATHLIB``, ou ``"<file>"`` para ``FILE-LENGTH``.
    reason : str
        Motivo da entrada (ex.: ``débito AD-005``).
    active : bool
        ``False`` desativa a entrada: ela não suprime nem é obsoleta.
    """

    rule_id: str
    path: str
    location: str
    reason: str
    active: bool = True


def location_of(violation: PolicyViolation) -> str:
    """Retorna a chave de localização de uma violação para matching.

    Para regras baseadas em símbolo (função/print) usa o símbolo; para regras
    baseadas em linha (``PATIENT-DATA``/``PATHLIB``) usa a linha como string.

    Parameters
    ----------
    violation : PolicyViolation
        Violação a localizar.

    Returns
    -------
    str
        Chave de localização (símbolo ou linha).
    """
    return violation.symbol if violation.symbol else str(violation.line)


def normalize_path(p: Path, base: Path | None = None) -> str:
    """Normaliza um caminho para string POSIX relativa à raiz.

    Relativiza ``p`` contra ``base`` (padrão: diretório de trabalho) e troca
    separadores ``\\`` (Windows) por ``/`` (POSIX), estabilizando as chaves de
    baseline entre sistemas operacionais.

    Parameters
    ----------
    p : Path
        Caminho do arquivo.
    base : Path | None
        Diretório raiz para relativização (padrão: ``Path.cwd()``).

    Returns
    -------
    str
        Caminho relativo com separadores POSIX.
    """
    base = base or Path.cwd()
    return Path(os.path.relpath(p, base)).as_posix()


def load_baseline(path: Path) -> tuple[BaselineEntry, ...]:
    """Carrega as entradas de baseline de um arquivo TOML.

    Um arquivo ausente retorna tupla vazia (nenhuma violação é mascarada);
    um TOML inválido propaga o erro para o chamador tratar como fatal.

    Parameters
    ----------
    path : Path
        Caminho do arquivo ``policies.toml``.

    Returns
    -------
    tuple[BaselineEntry, ...]
        Entradas de baseline ativas e inativas encontradas.

    Raises
    ------
    tomllib.TOMLDecodeError
        Quando o arquivo existe mas não é TOML válido.
    """
    if not path.exists():
        return ()
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    entries = []
    for raw in data.get("entries", []):
        entries.append(
            BaselineEntry(
                rule_id=str(raw["rule_id"]),
                path=str(raw["path"]),
                location=str(raw["location"]),
                reason=str(raw.get("reason", "")),
                active=bool(raw.get("active", True)),
            )
        )
    return tuple(entries)


def load_allowlist(path: Path) -> frozenset[str]:
    """Carrega os tokens sintéticos de ``[allowlist.patient_data]``.

    Arquivo ausente ou seção ausente retorna um conjunto vazio; as fixtures
    sintéticas padrão de ``rules.py`` continuam sempre ativas.

    Parameters
    ----------
    path : Path
        Caminho do arquivo ``policies.toml``.

    Returns
    -------
    frozenset[str]
        Tokens configurados, minúsculos, para suppressão de PATIENT-DATA.
    """
    if not path.exists():
        return frozenset()
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    raw = data.get("allowlist", {}).get("patient_data", [])
    if isinstance(raw, dict):
        raw = raw.get("tokens", [])
    return frozenset(str(token).lower() for token in raw)


def _escape(value: str) -> str:
    """Escapa uma string para ser usada como string básica TOML.

    Parameters
    ----------
    value : str
        Texto a escapar.

    Returns
    -------
    str
        Texto com ``\\``, aspas e quebras de linha escapados.
    """
    return (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )


def save_baseline(path: Path, entries: Sequence[BaselineEntry]) -> None:
    """Grava entradas de baseline em formato TOML.

    Serializador mínimo controlado pelo gerador: escreve apenas os campos das
    entradas (``active=false`` apenas quando desativada) e pode ser relido por
    ``load_baseline`` em round-trip idêntico.

    Parameters
    ----------
    path : Path
        Caminho de destino do arquivo ``policies.toml``.
    entries : Sequence[BaselineEntry]
        Entradas a persistir.
    """
    lines = [
        "# Baseline do policy checker "
        "(gerado por: python scripts/policy_check.py --baseline-generate)",
        "# Remove entradas conforme o débito é pago "
        "(AC-15 exige limpeza de obsoletas).",
        "",
    ]
    for entry in entries:
        lines.append("[[entries]]")
        lines.append(f'rule_id = "{_escape(entry.rule_id)}"')
        lines.append(f'path = "{_escape(entry.path)}"')
        lines.append(f'location = "{_escape(entry.location)}"')
        lines.append(f'reason = "{_escape(entry.reason)}"')
        if not entry.active:
            lines.append("active = false")
        lines.append("")
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def match(
    baseline: Sequence[BaselineEntry],
    rule_id: str,
    path: str,
    location: str,
) -> BaselineEntry | None:
    """Procura a entrada de baseline que cobre uma violação.

    A chave de matching é ``(rule_id, path, location)``. Entradas com
    ``active=False`` nunca suprimem violações.

    Parameters
    ----------
    baseline : Sequence[BaselineEntry]
        Entradas carregadas de ``policies.toml``.
    rule_id : str
        Identificador da regra violada.
    path : str
        Caminho POSIX relativo do arquivo.
    location : str
        Localização (símbolo ou linha) da violação.

    Returns
    -------
    BaselineEntry | None
        A primeira entrada que cobre a violação, ou ``None``.
    """
    for entry in baseline:
        if not entry.active:
            continue
        if (entry.rule_id, entry.path, entry.location) == (rule_id, path, location):
            return entry
    return None


def find_obsolete(
    violations: Sequence[PolicyViolation],
    entries: Sequence[BaselineEntry],
    files_scanned: Sequence[str],
) -> tuple[BaselineEntry, ...]:
    """Detecta entradas de baseline obsoletas no scan atual.

    Uma entrada ativa é obsoleta quando não existe violação correspondente no
    scan atual — o débito foi corrigido, o símbolo foi renomeado/removido ou o
    arquivo saiu da varredura (deletado/fora dos roots). Entradas inativas são
    ignoradas (nunca são obsoletas).

    Parameters
    ----------
    violations : Sequence[PolicyViolation]
        Violações encontradas no scan atual (antes da supressão por baseline).
    entries : Sequence[BaselineEntry]
        Entradas de baseline carregadas.
    files_scanned : Sequence[str]
        Caminhos POSIX relativos dos arquivos efetivamente varridos.

    Returns
    -------
    tuple[BaselineEntry, ...]
        Entradas ativas sem violação correspondente.
    """
    keys = {(v.rule_id, v.path, location_of(v)) for v in violations}
    scanned = set(files_scanned)
    obsolete = []
    for entry in entries:
        if not entry.active:
            continue
        key = (entry.rule_id, entry.path, entry.location)
        if key not in keys or entry.path not in scanned:
            obsolete.append(entry)
    return tuple(obsolete)
