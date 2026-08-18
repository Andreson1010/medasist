from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from langchain_text_splitters import RecursiveCharacterTextSplitter

from medasist.config import Settings
from medasist.ingestion.schemas import DocType, LoadedDocument

logger = logging.getLogger(__name__)

_MIN_CHUNK_LENGTH = 50

_SEPARATORS: dict[DocType, list[str]] = {
    DocType.BULA: ["\n\n", "\n", " "],
    DocType.DIRETRIZ: ["\n\n\n", "\n\n", "\n", " "],
    DocType.PROTOCOLO: ["\n\n", "\n", ". ", " "],
    DocType.MANUAL: ["\n\n", "\n", " "],
}

# Padrões de linha que parecem títulos de seção (ordem de verificação).
_SECTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Numeração hierárquica: "1. INTRODUÇÃO", "3.2.1 TÍTULO"
    re.compile(r"^\s*\d+(\.\d+)*[\.\)]?\s+\S+"),
    # Título em CAIXA ALTA, ex: "QUAIS AS CONTRAINDICAÇÕES DO X?"
    re.compile(r"^[A-ZÀ-Ú][A-ZÀ-Ú\s0-9/()%.,:?-]{2,}$"),
)


@dataclass(frozen=True)
class TextChunk:
    """Chunk de texto extraído de um documento médico.

    Attributes
    ----------
    text : str
        Conteúdo textual do chunk.
    doc_type : DocType
        Tipo do documento de origem.
    source_path : Path
        Caminho do arquivo PDF de origem.
    sha256 : str
        Hash SHA-256 do documento pai.
    chunk_index : int
        Posição do chunk na lista (0-based).
    page : int | None
        Número da página de origem (1-based), quando conhecido.
    section : str
        Título da seção vigente no ponto do documento, vazio se não detectado.
    """

    text: str
    doc_type: DocType
    source_path: Path
    sha256: str
    chunk_index: int
    page: int | None = None
    section: str = ""


def _get_splitter(
    doc_type: DocType, settings: Settings
) -> RecursiveCharacterTextSplitter:
    """Retorna o splitter configurado para o DocType informado.

    Parameters
    ----------
    doc_type : DocType
        Tipo do documento.
    settings : Settings
        Configurações com chunk_size e chunk_overlap por DocType.

    Returns
    -------
    RecursiveCharacterTextSplitter
        Splitter pronto para uso.
    """
    chunk_size = getattr(settings, f"chunk_size_{doc_type.value}")
    chunk_overlap = getattr(settings, f"chunk_overlap_{doc_type.value}")
    return RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=_SEPARATORS[doc_type],
    )


def chunk_document(doc: LoadedDocument, settings: Settings) -> list[TextChunk]:
    """Divide um documento em chunks de texto por estratégia de DocType.

    Cada página é dividida separadamente, preservando o número da página em
    cada chunk. Títulos de seção detectados na página (numeração hierárquica
    ou caixa alta) são anexados como ``section`` do chunk.

    Parameters
    ----------
    doc : LoadedDocument
        Documento carregado do disco.
    settings : Settings
        Configurações com tamanhos e overlaps por DocType.

    Returns
    -------
    list[TextChunk]
        Lista de chunks com metadados, excluindo textos curtos (< 50 chars).
    """
    if not doc.pages:
        logger.debug("Documento vazio: %s", doc.path)
        return []

    splitter = _get_splitter(doc.doc_type, settings)
    chunks: list[TextChunk] = []
    index = 0
    for page in doc.pages:
        if not page.text.strip():
            continue
        sections = _detect_sections(page.text, settings)
        raw_chunks = splitter.split_text(page.text)

        search_from = 0
        for raw in raw_chunks:
            if len(raw) < _MIN_CHUNK_LENGTH:
                logger.debug("Chunk ignorado (muito curto): %d chars", len(raw))
                continue
            start = page.text.find(raw, search_from)
            if start == -1:
                start = search_from
            search_from = start
            chunks.append(
                TextChunk(
                    text=raw,
                    doc_type=doc.doc_type,
                    source_path=doc.path,
                    sha256=doc.sha256,
                    chunk_index=index,
                    page=page.page_number,
                    section=_section_at_offset(sections, start),
                )
            )
            index += 1

    logger.info(
        "Documento %s → %d chunks (%s)", doc.path.name, len(chunks), doc.doc_type
    )
    return chunks


def _detect_sections(text: str, settings: Settings) -> list[tuple[int, str]]:
    """Detecta títulos de seção em um texto de página.

    Retorna pares ``(offset, título)`` com o deslocamento de cada título
    dentro do texto e o título em si. Linhas muito curtas, números de página
    e cabeçalhos/rodapés repetidos são ignorados.

    Parameters
    ----------
    text : str
        Texto da página.
    settings : Settings
        Configurações com limites de comprimento de título.

    Returns
    -------
    list[tuple[int, str]]
        Lista ordenada de ``(offset, título)`` por posição no texto.
    """
    sections: list[tuple[int, str]] = []
    offset = 0
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if _is_section_heading(stripped, settings):
            sections.append((offset, stripped))
        offset += len(line)
    return sections


def _is_section_heading(line: str, settings: Settings) -> bool:
    """Verifica se uma linha parece um título de seção.

    Parameters
    ----------
    line : str
        Linha já sem espaços nas bordas.
    settings : Settings
        Configurações com limites de comprimento de título.

    Returns
    -------
    bool
        ``True`` se a linha parece um título de seção.
    """
    if not line:
        return False
    min_len = settings.section_heading_min_len
    max_len = settings.section_heading_max_len
    if not (min_len <= len(line) <= max_len):
        return False
    if line.isdigit():  # número de página
        return False
    return any(pattern.match(line) for pattern in _SECTION_PATTERNS)


def _section_at_offset(sections: list[tuple[int, str]], offset: int) -> str:
    """Retorna o título de seção vigente em um deslocamento do texto.

    Parameters
    ----------
    sections : list[tuple[int, str]]
        Títulos de seção detectados, ordenados por offset.
    offset : int
        Deslocamento do chunk dentro da página.

    Returns
    -------
    str
        Título da seção vigente (último título antes do offset), ou vazio.
    """
    current = ""
    for section_offset, title in sections:
        if section_offset <= offset:
            current = title
        else:
            break
    return current
