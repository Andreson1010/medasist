from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from medasist.config import Settings


class DocType(StrEnum):
    """Tipo de documento médico ingerido.

    O valor string corresponde ao sufixo usado em ``Settings``
    (ex: ``chunk_size_bula``, ``collection_bulas``).
    """

    BULA = "bula"
    DIRETRIZ = "diretriz"
    PROTOCOLO = "protocolo"
    MANUAL = "manual"


@dataclass(frozen=True)
class PageContent:
    """Conteúdo extraído de uma página de PDF.

    Attributes
    ----------
    page_number : int
        Número da página (1-based).
    text : str
        Texto extraído da página.
    """

    page_number: int
    text: str


@dataclass(frozen=True)
class LoadedDocument:
    """Documento carregado do disco, pronto para chunking.

    Attributes
    ----------
    path : Path
        Caminho absoluto do arquivo PDF.
    doc_type : DocType
        Tipo do documento, usado para selecionar estratégia de chunking.
    pages : list[PageContent]
        Páginas extraídas, em ordem.
    sha256 : str
        Hash SHA-256 do conteúdo do arquivo, usado para idempotência.
    """

    path: Path
    doc_type: DocType
    sha256: str
    pages: list[PageContent] = field(default_factory=list)

    @property
    def full_text(self) -> str:
        """Texto completo do documento (todas as páginas concatenadas)."""
        return "\n".join(p.text for p in self.pages if p.text.strip())


_COLLECTION_ACCESSORS: dict[DocType, Callable[[Settings], str]] = {
    DocType.BULA: lambda settings: settings.collection_bulas,
    DocType.DIRETRIZ: lambda settings: settings.collection_diretrizes,
    DocType.PROTOCOLO: lambda settings: settings.collection_protocolos,
    DocType.MANUAL: lambda settings: settings.collection_manuais,
}


def collection_name(doc_type: DocType, settings: Settings) -> str:
    """Retorna o nome da coleção ChromaDB configurado para o DocType.

    Ponto único de verdade do mapeamento ``DocType`` → campo de ``Settings``.
    O acesso é tipado (acessadores em vez de ``getattr`` por string), então
    renomear um campo ``collection_*`` em ``Settings`` é detectado por
    typecheck e pelos testes de exaustividade, não apenas em runtime.

    Parameters
    ----------
    doc_type : DocType
        Tipo do documento, determina qual coleção usar.
    settings : Settings
        Configurações com os nomes de coleção (``collection_*``).

    Returns
    -------
    str
        Nome da coleção ChromaDB correspondente ao ``doc_type``.

    Raises
    ------
    ValueError
        Se ``doc_type`` não tiver coleção mapeada.
    """
    accessor = _COLLECTION_ACCESSORS.get(doc_type)
    if accessor is None:
        raise ValueError(f"DocType sem coleção mapeada: {doc_type!r}")
    return accessor(settings)
