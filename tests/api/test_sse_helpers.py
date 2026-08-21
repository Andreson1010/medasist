from __future__ import annotations

import json

from medasist.api.schemas import (
    CitationResponse,
    sse_citations,
    sse_cold_start,
    sse_disclaimer,
    sse_done,
    sse_error,
    sse_token,
)


class TestSseToken:
    def test_format(self) -> None:
        assert sse_token("olá") == 'data: {"type": "token", "delta": "olá"}\n\n'

    def test_empty_delta(self) -> None:
        assert sse_token("") == 'data: {"type": "token", "delta": ""}\n\n'


class TestSseCitations:
    def test_serializes_citation_responses(self) -> None:
        item = CitationResponse(index=1, source="b.pdf", section="Posologia", page="3")
        line = sse_citations([item])
        assert line.startswith("data: ")
        payload = json.loads(line[6:].strip())
        assert payload["type"] == "citations"
        assert payload["citations"] == [
            {"index": 1, "source": "b.pdf", "section": "Posologia", "page": "3"}
        ]

    def test_empty_list(self) -> None:
        line = sse_citations([])
        payload = json.loads(line[6:].strip())
        assert payload == {"type": "citations", "citations": []}


class TestSseDisclaimer:
    def test_format(self) -> None:
        assert (
            sse_disclaimer("aviso")
            == 'data: {"type": "disclaimer", "text": "aviso"}\n\n'
        )


class TestSseColdStart:
    def test_format(self) -> None:
        assert (
            sse_cold_start("msg")
            == 'data: {"type": "cold_start", "message": "msg"}\n\n'
        )


class TestSseError:
    def test_format(self) -> None:
        assert sse_error("erro") == 'data: {"type": "error", "message": "erro"}\n\n'


class TestSseDone:
    def test_format(self) -> None:
        assert sse_done() == 'data: {"type": "done"}\n\n'


class TestSseFraming:
    def test_events_end_with_double_newline(self) -> None:
        for line in (sse_token("a"), sse_citations([]), sse_done(), sse_error("x")):
            assert line.endswith("\n\n")

    def test_utf8_preserved(self) -> None:
        assert "olá" in sse_token("olá")
        assert "ç" in sse_disclaimer("informação clínica")
        assert "ã" in sse_cold_start("não encontrei")
