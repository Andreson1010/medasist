from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest
from langchain_core.documents import Document
from pydantic import SecretStr

from medasist.config import Settings
from medasist.retrieval import reranker
from medasist.retrieval.reranker import rerank_documents

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _settings(**overrides: object) -> Settings:
    defaults: dict[str, object] = {"retrieval_rerank_enabled": True}
    defaults.update(overrides)
    return Settings(
        admin_api_key=SecretStr("very-strong-key-0123456789"),
        **defaults,
    )


def _docs(*contents: str) -> list[tuple[Document, float]]:
    return [(Document(page_content=c), float(i + 1)) for i, c in enumerate(contents)]


def _contents(pairs: list[tuple[Document, float]]) -> list[str]:
    return [doc.page_content for doc, _ in pairs]


@pytest.fixture(autouse=True)
def _reset_reranker():
    """Zera o singleton global antes e depois de cada teste."""
    reranker._reranker = None
    yield
    reranker._reranker = None


@pytest.fixture
def mock_cross_encoder(mocker: MagicMock):
    """Patcheia ``sentence_transformers.CrossEncoder`` com predict controlado."""
    instance = MagicMock()
    mock_cls = mocker.patch("sentence_transformers.CrossEncoder", return_value=instance)
    return mock_cls, instance


# ---------------------------------------------------------------------------
# Tests — rerank_documents
# ---------------------------------------------------------------------------


class TestRerankDocuments:
    def test_reorders_by_score_desc(self, mock_cross_encoder) -> None:
        mock_cls, instance = mock_cross_encoder
        # Ordem L2: A, B, C. Scores do reranker invertem: C > B > A
        instance.predict.return_value = [0.1, 0.5, 0.9]
        docs = _docs("A", "B", "C")

        result = rerank_documents(docs, "query", _settings())

        assert _contents(result) == ["C", "B", "A"]
        mock_cls.assert_called_once()

    def test_single_batch_over_up_to_rerank_top_n(self, mock_cross_encoder) -> None:
        _, instance = mock_cross_encoder
        instance.predict.return_value = [1.0, 0.0, 0.0]
        docs = _docs("A", "B", "C")
        settings = _settings(retrieval_rerank_top_n=2)

        rerank_documents(docs, "query", settings)

        instance.predict.assert_called_once()
        pairs = instance.predict.call_args.args[0]
        assert len(pairs) == 2
        assert [p[0] for p in pairs] == ["query", "query"]

    def test_batch_size_forwarded_to_predict(self, mock_cross_encoder) -> None:
        _, instance = mock_cross_encoder
        instance.predict.return_value = [1.0, 0.0, 0.0]
        docs = _docs("A", "B", "C")
        settings = _settings(retrieval_rerank_batch_size=4)

        rerank_documents(docs, "query", settings)

        assert instance.predict.call_args.kwargs["batch_size"] == 4

    def test_deterministic_tie_break_preserves_l2_order(
        self, mock_cross_encoder
    ) -> None:
        _, instance = mock_cross_encoder
        # Empates em 0.9 (A,B) e 0.2 (C,D): ordenação estável mantém ordem L2
        instance.predict.return_value = [0.9, 0.9, 0.2, 0.2]
        docs = _docs("A", "B", "C", "D")

        result = rerank_documents(docs, "query", _settings())

        assert _contents(result) == ["A", "B", "C", "D"]

    def test_docs_beyond_top_n_kept_in_l2_order_at_end(
        self, mock_cross_encoder
    ) -> None:
        _, instance = mock_cross_encoder
        instance.predict.return_value = [1.0, 0.0]
        docs = _docs("A", "B", "C", "D")
        settings = _settings(retrieval_rerank_top_n=2)

        result = rerank_documents(docs, "query", settings)

        # A e B pontuados (A>B); C e D (não pontuados) preservam ordem L2 no fim
        assert _contents(result) == ["A", "B", "C", "D"]

    def test_failure_returns_l2_order_logs_and_does_not_propagate(
        self, mock_cross_encoder, caplog
    ) -> None:
        _, instance = mock_cross_encoder
        instance.predict.side_effect = RuntimeError("timeout do reranker")
        docs = _docs("A", "B", "C")

        with caplog.at_level(logging.ERROR, logger="medasist.retrieval.reranker"):
            result = rerank_documents(docs, "query", _settings())

        assert result is docs
        assert _contents(result) == ["A", "B", "C"]
        assert any(
            r.levelno == logging.ERROR and "Reranker falhou" in r.getMessage()
            for r in caplog.records
        )

    def test_model_load_failure_is_caught(self, mocker: MagicMock) -> None:
        # CrossEncoder() levanta durante o lazy load → degrada para L2
        mocker.patch(
            "sentence_transformers.CrossEncoder",
            side_effect=RuntimeError("modelo ausente"),
        )
        docs = _docs("A", "B")

        result = rerank_documents(docs, "query", _settings())

        assert _contents(result) == ["A", "B"]

    def test_disabled_returns_docs_unchanged_without_instantiating_model(
        self, mocker: MagicMock
    ) -> None:
        mock_cls = mocker.patch("sentence_transformers.CrossEncoder")
        docs = _docs("A", "B", "C")

        result = rerank_documents(
            docs, "query", _settings(retrieval_rerank_enabled=False)
        )

        assert result is docs
        mock_cls.assert_not_called()

    def test_empty_docs_returns_empty_without_calling_predict(
        self, mock_cross_encoder
    ) -> None:
        _, instance = mock_cross_encoder

        result = rerank_documents([], "query", _settings())

        assert result == []
        instance.predict.assert_not_called()


# ---------------------------------------------------------------------------
# Tests — singleton lazy
# ---------------------------------------------------------------------------


class TestRerankerSingleton:
    def test_loaded_once_across_calls(self, mock_cross_encoder) -> None:
        mock_cls, instance = mock_cross_encoder
        instance.predict.return_value = [1.0, 0.0]
        docs = _docs("A", "B")

        rerank_documents(docs, "query", _settings())
        rerank_documents(docs, "query", _settings())

        mock_cls.assert_called_once()

    def test_model_config_from_settings(self, mock_cross_encoder) -> None:
        mock_cls, _ = mock_cross_encoder
        settings = _settings(retrieval_rerank_model="custom-reranker")
        rerank_documents(_docs("A"), "query", settings)
        assert mock_cls.call_args.args[0] == "custom-reranker"
