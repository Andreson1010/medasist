from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class DocType(str, Enum):
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
