from __future__ import annotations

import pytest
from langchain_core.documents import Document

from medasist.generation.citations import CitationItem, build_citations, validate_citations


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_doc(content: str, source: str, section: str = "", page: str = "") -> Document:
    return Document(
        page_content=content,
        metadata={"source": source, "section": section, "page": page},
    )


# ---------------------------------------------------------------------------
# build_citations
# ---------------------------------------------------------------------------


class TestBuildCitations:
    def test_returns_one_item_per_doc(self) -> None:
        docs = [
            _make_doc("texto A", source="bula_x.pdf"),
            _make_doc("texto B", source="diretriz_y.pdf"),
        ]
        result = build_citations(docs)
        assert len(result) == 2

    def test_indices_start_at_one(self) -> None:
        docs = [_make_doc("x", source="a.pdf"), _make_doc("y", source="b.pdf")]
        indices = [c.index for c in build_citations(docs)]
        assert indices == [1, 2]

    def test_extracts_source(self) -> None:
        docs = [_make_doc("x", source="bula_amoxicilina.pdf")]
        result = build_citations(docs)
        assert result[0].source == "bula_amoxicilina.pdf"

    def test_extracts_section_and_page(self) -> None:
        docs = [_make_doc("x", source="a.pdf", section="Posologia", page="12")]
        item = build_citations(docs)[0]
        assert item.section == "Posologia"
        assert item.page == "12"

    def test_missing_section_and_page_default_to_empty_string(self) -> None:
        doc = Document(page_content="x", metadata={"source": "a.pdf"})
        item = build_citations([doc])[0]
        assert item.section == ""
        assert item.page == ""

    def test_empty_docs_returns_empty_list(self) -> None:
        assert build_citations([]) == []

    def test_citation_item_is_immutable(self) -> None:
        docs = [_make_doc("x", source="a.pdf")]
        item = build_citations(docs)[0]
        with pytest.raises((AttributeError, TypeError)):
            item.source = "outro.pdf"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# validate_citations
# ---------------------------------------------------------------------------


class TestValidateCitations:
    def test_keeps_cited_items(self) -> None:
        citations = [
            CitationItem(index=1, source="a.pdf", section="S1", page="1"),
            CitationItem(index=2, source="b.pdf", section="S2", page="2"),
        ]
        answer = "Veja [1] e também [2]."
        _, valid = validate_citations(answer, citations)
        assert len(valid) == 2

    def test_removes_orphan_citations(self) -> None:
        citations = [
            CitationItem(index=1, source="a.pdf", section="", page=""),
            CitationItem(index=2, source="b.pdf", section="", page=""),
        ]
        answer = "Apenas [1] foi usado."
        _, valid = validate_citations(answer, citations)
        assert len(valid) == 1
        assert valid[0].index == 1

    def test_answer_unchanged_when_all_valid(self) -> None:
        citations = [CitationItem(index=1, source="a.pdf", section="", page="")]
        answer = "Resposta com [1]."
        result_answer, _ = validate_citations(answer, citations)
        assert result_answer == answer

    def test_no_citations_in_answer_returns_empty_list(self) -> None:
        citations = [CitationItem(index=1, source="a.pdf", section="", page="")]
        answer = "Resposta sem referências."
        _, valid = validate_citations(answer, citations)
        assert valid == []

    def test_empty_citations_list_returns_empty(self) -> None:
        answer = "Resposta com [1]."
        _, valid = validate_citations(answer, [])
        assert valid == []

    def test_duplicate_refs_in_answer_deduplicated(self) -> None:
        citations = [CitationItem(index=1, source="a.pdf", section="", page="")]
        answer = "[1] texto [1] mais texto [1]."
        _, valid = validate_citations(answer, citations)
        assert len(valid) == 1
