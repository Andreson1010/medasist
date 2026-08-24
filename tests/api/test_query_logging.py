from __future__ import annotations

import logging
from unittest.mock import MagicMock

import chromadb
import pytest
from fastapi.testclient import TestClient
from langchain_core.embeddings import Embeddings
from langchain_core.messages import AIMessage
from pydantic import SecretStr

from medasist.config import Settings
from medasist.generation.chain import run_query
from medasist.ingestion.schemas import DocType
from medasist.profiles.schemas import UserProfile
from medasist.vectorstore.store import get_vectorstore


class TestQueryLogging:
    def test_log_includes_latency_and_doc_types(
        self, client: TestClient, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Log do /query inclui latency_ms e doc_types (com cold_start e citations)."""
        with caplog.at_level(logging.INFO, logger="medasist.api.routers.query"):
            response = client.post(
                "/query",
                json={
                    "question": "qual a dose de Zolatril?",
                    "profile": "medico",
                    "doc_types": ["bula"],
                },
            )

        assert response.status_code == 200
        records = [r for r in caplog.records if r.getMessage().startswith("query:")]
        assert records, "nenhum record de query capturado"
        message = records[0].getMessage()
        assert "latency_ms=" in message
        assert "doc_types=['bula']" in message
        assert "cold_start=False" in message
        assert "citations=1" in message
        assert "profile='medico'" in message

    def test_log_doc_types_none_when_not_filtered(
        self, client: TestClient, caplog: pytest.LogCaptureFixture
    ) -> None:
        """doc_types ausente no request é refletido como None no log."""
        with caplog.at_level(logging.INFO, logger="medasist.api.routers.query"):
            response = client.post(
                "/query",
                json={"question": "qual a dose?", "profile": "medico"},
            )

        assert response.status_code == 200
        records = [r for r in caplog.records if r.getMessage().startswith("query:")]
        assert records, "nenhum record de query capturado"
        assert "doc_types=None" in records[0].getMessage()


class TestCompoundQueryLogging:
    def _decompose_settings(self) -> Settings:
        """Settings com decomposição ativa para o log composto."""
        return Settings(
            admin_api_key=SecretStr("test-admin-key-0123456789"),
            retrieval_top_k=10,
            retrieval_score_threshold=0.4,
            retrieval_query_rewrite_enabled=False,
            retrieval_decompose_enabled=True,
            retrieval_decompose_max_sub_questions=5,
            retrieval_decompose_min_tokens=4,
        )

    def test_compound_log_emitted_with_counts_and_no_data_leak(
        self, mocker: MagicMock, caplog: pytest.LogCaptureFixture, tmp_path
    ) -> None:
        """Com pergunta composta + decomposição ativa, o log composto de
        ``run_query`` é emitido com nº de sub-perguntas/hits/misses corretos e
        sem vazar o texto da pergunta."""
        settings = self._decompose_settings()

        class _TopicEmbeddings(Embeddings):
            def embed_documents(self, texts):
                return [
                    (
                        [1.0, 0.0, 0.0, 0.0]
                        if "dose" in t.lower()
                        else [0.0, 1.0, 0.0, 0.0]
                    )
                    for t in texts
                ]

            def embed_query(self, text):
                lower = text.lower()
                if "dose" in lower:
                    return [1.0, 0.0, 0.0, 0.0]
                if "álcool" in lower or "alcool" in lower:
                    return [0.0, 1.0, 0.0, 0.0]
                return [0.5, 0.5, 0.0, 0.0]

        client = chromadb.PersistentClient(path=str(tmp_path / "chroma"))
        store = get_vectorstore(DocType.BULA, client, _TopicEmbeddings(), settings)
        store.add_texts(
            texts=[
                "A dose de dipirona é 500 mg por via oral.",
                "Não tomar dipirona com álcool durante o tratamento.",
            ],
            metadatas=[
                {
                    "doc_type": "bula",
                    "source_path": "bula_dipirona.pdf",
                    "page": "2",
                    "section": "Posologia",
                },
                {
                    "doc_type": "bula",
                    "source_path": "bula_dipirona.pdf",
                    "page": "4",
                    "section": "Interações",
                },
            ],
            ids=["bula_000", "bula_001"],
        )
        stores = {DocType.BULA: store}

        split_instance = MagicMock()
        split_instance.return_value = AIMessage(
            content="Qual a dose de dipirona?\nPosso tomar dipirona com álcool?"
        )
        mocker.patch(
            "medasist.retrieval.decompose.ChatOpenAI", return_value=split_instance
        )
        gen_instance = MagicMock()
        gen_instance.side_effect = [
            AIMessage(content="Dose de dipirona: 500 mg [1]."),
            AIMessage(content="Evite álcool durante o tratamento [1]."),
        ]
        mocker.patch("medasist.generation.chain.ChatOpenAI", return_value=gen_instance)

        question = "Qual a dose de dipirona e posso tomar com álcool?"
        with caplog.at_level(logging.INFO, logger="medasist.generation.chain"):
            run_query(question, stores, UserProfile.MEDICO, settings)

        records = [
            r
            for r in caplog.records
            if r.getMessage().startswith("run_query: pergunta composta")
        ]
        assert records, "nenhum record de log composto capturado"
        message = records[0].getMessage()
        assert "2 sub-pergunta(s)" in message
        assert "hits=2" in message
        assert "misses=0" in message
        assert "unanswered=0" in message
        assert "cold_start=False" in message
        # sem vazar dados: o texto da pergunta nunca aparece no log composto
        assert question not in message
        assert "dipirona" not in message


class TestQueryStreamLogging:
    def test_stream_log_includes_profile_cold_start_citations_and_latency(
        self, streaming_client: TestClient, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Log do /query/stream inclui profile, cold_start, citations e latency."""
        with caplog.at_level(logging.INFO, logger="medasist.api.routers.query"):
            response = streaming_client.post(
                "/query/stream",
                json={
                    "question": "qual a dose de Zolatril?",
                    "profile": "medico",
                    "doc_types": ["bula"],
                },
            )

        assert response.status_code == 200
        records = [
            r for r in caplog.records if r.getMessage().startswith("query/stream:")
        ]
        assert records, "nenhum record de query/stream capturado"
        message = records[0].getMessage()
        assert "profile='medico'" in message
        assert "cold_start=False" in message
        assert "citations=1" in message
        assert "latency_ms=" in message
        assert "doc_types=['bula']" in message

    def test_stream_cold_start_log_reflects_flag(
        self,
        streaming_client_factory,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Cold start por retrieval vazio é refletido como cold_start=True no log."""
        from medasist.profiles.schemas import UserProfile

        def _cold(question: str, doc_types=None):  # type: ignore[no-untyped-def]
            def gen() -> None:
                yield from ()
                return [], True

            return gen()

        chains = {p: MagicMock(side_effect=_cold) for p in UserProfile}

        with (
            caplog.at_level(logging.INFO, logger="medasist.api.routers.query"),
            streaming_client_factory(chains) as c,
        ):
            response = c.post(
                "/query/stream",
                json={"question": "qual a dose?", "profile": "medico"},
            )

        assert response.status_code == 200
        records = [
            r for r in caplog.records if r.getMessage().startswith("query/stream:")
        ]
        assert records, "nenhum record de query/stream capturado"
        message = records[0].getMessage()
        assert "cold_start=True" in message
        assert "citations=0" in message
