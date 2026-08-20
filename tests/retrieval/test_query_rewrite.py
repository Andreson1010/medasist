from __future__ import annotations

import logging
from unittest.mock import MagicMock

from langchain_core.messages import AIMessage
from pydantic import SecretStr

from medasist.config import Settings
from medasist.retrieval import query_rewrite
from medasist.retrieval.query_rewrite import _is_short, rewrite_query

_ADMIN_KEY = "very-strong-key-0123456789"


def _settings(**overrides: object) -> Settings:
    """Settings com reescrita habilitada e overrides por critério."""
    defaults: dict[str, object] = {"retrieval_query_rewrite_enabled": True}
    defaults.update(overrides)
    return Settings(admin_api_key=SecretStr(_ADMIN_KEY), **defaults)


def _mock_llm(mocker, content: str) -> tuple[MagicMock, MagicMock]:
    """Patcheia ``ChatOpenAI`` no local real do módulo.

    Retorna ``(mock_cls, instance)``; ``instance`` retorna um ``AIMessage``
    com o ``content`` dado quando invocada pela chain de reescrita.
    """
    instance = MagicMock()
    instance.return_value = AIMessage(content=content)
    mock_cls = mocker.patch(
        "medasist.retrieval.query_rewrite.ChatOpenAI", return_value=instance
    )
    return mock_cls, instance


# ---------------------------------------------------------------------------
# Testes — heurística _is_short
# ---------------------------------------------------------------------------


class TestIsShort:
    def test_single_content_token_is_short(self) -> None:
        assert _is_short("dipirona", _settings()) is True

    def test_two_content_tokens_below_min_length_is_short(self) -> None:
        assert _is_short("dipirona febre", _settings()) is True

    def test_exactly_min_length_content_tokens_is_not_short(self) -> None:
        # min_length=3; "dipirona febre dor" tem exatamente 3 tokens de conteúdo
        assert _is_short("dipirona febre dor", _settings()) is False

    def test_above_min_length_is_not_short(self) -> None:
        assert _is_short("dipirona febre dor intensa", _settings()) is False

    def test_stopwords_only_is_short(self) -> None:
        # "qual a dose para" são todas stopwords → zero tokens de conteúdo
        assert _is_short("qual a dose para", _settings()) is True

    def test_empty_query_is_short(self) -> None:
        assert _is_short("", _settings()) is True

    def test_custom_min_length_boundary(self) -> None:
        # min_length=2: exatamente 2 tokens de conteúdo → NÃO curta
        assert (
            _is_short("dipirona febre", _settings(retrieval_query_rewrite_min_length=2))
            is False
        )
        # 1 token de conteúdo → curta
        assert (
            _is_short("dipirona", _settings(retrieval_query_rewrite_min_length=2))
            is True
        )


# ---------------------------------------------------------------------------
# Testes — rewrite_query (identidade flag off / não-curta)
# ---------------------------------------------------------------------------


class TestRewriteIdentity:
    def test_flag_off_returns_query_without_llm(self, mocker) -> None:
        mock_cls = mocker.patch("medasist.retrieval.query_rewrite.ChatOpenAI")
        settings = _settings(retrieval_query_rewrite_enabled=False)

        result = rewrite_query("dipirona", settings)

        assert result == "dipirona"
        mock_cls.assert_not_called()

    def test_not_short_returns_query_without_llm(self, mocker) -> None:
        mock_cls = mocker.patch("medasist.retrieval.query_rewrite.ChatOpenAI")
        settings = _settings()

        result = rewrite_query("dipirona febre dor", settings)

        assert result == "dipirona febre dor"
        mock_cls.assert_not_called()


# ---------------------------------------------------------------------------
# Testes — rewrite_query (expansão e modelo)
# ---------------------------------------------------------------------------


class TestRewriteExpansion:
    def test_short_query_expands_via_llm(self, mocker) -> None:
        _mock_llm(mocker, "Qual a dose de dipirona para adultos?")

        result = rewrite_query("dipirona", _settings())

        assert result == "Qual a dose de dipirona para adultos?"

    def test_llm_constructed_with_resolved_model_and_temperature(self, mocker) -> None:
        mock_cls, _ = _mock_llm(mocker, "Qual a dose de dipirona?")
        settings = _settings(
            retrieval_query_rewrite_model="custom-rewriter",
            retrieval_query_rewrite_temperature=0.0,
            retrieval_query_rewrite_max_tokens=64,
        )

        rewrite_query("dipirona", settings)

        call_kwargs = mock_cls.call_args.kwargs
        assert call_kwargs["model"] == "custom-rewriter"
        assert call_kwargs["temperature"] == 0.0
        assert call_kwargs["max_tokens"] == 64
        assert call_kwargs["max_retries"] == settings.llm_max_retries
        assert call_kwargs["request_timeout"] == settings.llm_request_timeout

    def test_empty_model_resolves_to_lm_studio_llm_model(self, mocker) -> None:
        mock_cls, _ = _mock_llm(mocker, "Qual a dose de dipirona?")
        settings = _settings(
            lm_studio_llm_model="phi-3-mini",
            retrieval_query_rewrite_model="",
        )

        rewrite_query("dipirona", settings)

        assert mock_cls.call_args.kwargs["model"] == "phi-3-mini"


# ---------------------------------------------------------------------------
# Testes — rewrite_query (degradação graciosa)
# ---------------------------------------------------------------------------


class TestRewriteDegradation:
    def test_llm_failure_returns_original_and_logs(self, mocker, caplog) -> None:
        instance = MagicMock()
        instance.side_effect = RuntimeError("timeout do LM Studio")
        mocker.patch(
            "medasist.retrieval.query_rewrite.ChatOpenAI", return_value=instance
        )
        settings = _settings()

        with caplog.at_level(logging.ERROR, logger="medasist.retrieval.query_rewrite"):
            result = rewrite_query("dipirona", settings)

        assert result == "dipirona"
        assert any(
            r.levelno == logging.ERROR and "Query rewrite falhou" in r.getMessage()
            for r in caplog.records
        )

    def test_empty_output_returns_original(self, mocker) -> None:
        _mock_llm(mocker, "   ")
        assert rewrite_query("dipirona", _settings()) == "dipirona"

    def test_punctuation_only_output_returns_original(self, mocker) -> None:
        _mock_llm(mocker, "!!! ... ???")
        assert rewrite_query("dipirona", _settings()) == "dipirona"

    def test_long_output_truncated_to_max_output(self, mocker) -> None:
        _mock_llm(mocker, "x" * 500)
        settings = _settings(retrieval_query_rewrite_max_output=50)

        result = rewrite_query("dipirona", settings)

        assert result == "x" * 50

    def test_whitespace_padded_output_is_stripped(self, mocker) -> None:
        _mock_llm(mocker, "  Qual a dose de dipirona para adultos?  ")
        result = rewrite_query("dipirona", _settings())
        assert result == "Qual a dose de dipirona para adultos?"


# ---------------------------------------------------------------------------
# Testes — prompt de expansão proíbe preâmbulo/cochicho
# ---------------------------------------------------------------------------


class TestExpansionPrompt:
    def test_prompt_forbids_preamble_and_commentary(self) -> None:
        template = query_rewrite._EXPANSION_PROMPT.template.lower()
        assert "preâmbulo" in template or "preambulo" in template
        assert "apenas" in template
        assert "{query}" in query_rewrite._EXPANSION_PROMPT.template
