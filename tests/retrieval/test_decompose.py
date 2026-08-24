from __future__ import annotations

import logging
from unittest.mock import MagicMock

from langchain_core.messages import AIMessage
from pydantic import SecretStr

from medasist.config import Settings
from medasist.retrieval import decompose
from medasist.retrieval.decompose import _is_compound, decompose_query

_ADMIN_KEY = "very-strong-key-0123456789"

_COMPOUND_OU = "Qual a dose de Alphazol ou posso tomar com Betazol?"


def _settings(**overrides: object) -> Settings:
    """Settings com decomposição habilitada e overrides por critério."""
    defaults: dict[str, object] = {
        "retrieval_decompose_enabled": True,
        "retrieval_decompose_max_sub_questions": 5,
        "retrieval_decompose_min_content_tokens": 4,
    }
    defaults.update(overrides)
    return Settings(admin_api_key=SecretStr(_ADMIN_KEY), **defaults)


def _mock_split_llm(mocker, content: str) -> tuple[MagicMock, MagicMock]:
    """Patcheia ``ChatOpenAI`` no local real do módulo de split.

    Retorna ``(mock_cls, instance)``; ``instance`` retorna um ``AIMessage``
    com o ``content`` dado quando invocada pela chain de decomposição.
    """
    instance = MagicMock()
    instance.return_value = AIMessage(content=content)
    mock_cls = mocker.patch(
        "medasist.retrieval.decompose.ChatOpenAI", return_value=instance
    )
    return mock_cls, instance


# ---------------------------------------------------------------------------
# Testes — heurística _is_compound (Q4)
# ---------------------------------------------------------------------------


class TestIsCompound:
    def test_single_token_not_compound(self) -> None:
        assert _is_compound("Alphazol", _settings()) is False

    def test_no_connector_not_compound(self) -> None:
        # >= 4 tokens de conteúdo, mas sem conector nem vírgula
        assert _is_compound("Alphazol causa sonolência intensa", _settings()) is False

    def test_connector_ou_is_compound(self) -> None:
        assert _is_compound(_COMPOUND_OU, _settings()) is True

    def test_comma_is_compound(self) -> None:
        query = "Alphazol causa sonolência, Betazol causa insônia?"
        assert _is_compound(query, _settings()) is True

    def test_connector_below_min_content_tokens_not_compound(self) -> None:
        # "Alphazol ou Betazol" tem 3 tokens de conteúdo (< 4)
        assert _is_compound("Alphazol ou Betazol", _settings()) is False

    def test_boundary_min_content_tokens(self) -> None:
        # com min=3, "Alphazol ou Betazol" (3 tokens, "ou" conector) é composta
        assert (
            _is_compound(
                "Alphazol ou Betazol",
                _settings(retrieval_decompose_min_content_tokens=3),
            )
            is True
        )

    def test_stopwords_only_not_compound(self) -> None:
        assert _is_compound("qual a dose para", _settings()) is False

    def test_empty_query_not_compound(self) -> None:
        assert _is_compound("", _settings()) is False


# ---------------------------------------------------------------------------
# Testes — decompose_query (identidade flag off / não-composta)
# ---------------------------------------------------------------------------


class TestDecomposeIdentity:
    def test_flag_off_returns_query_without_llm(self, mocker) -> None:
        mock_cls = mocker.patch("medasist.retrieval.decompose.ChatOpenAI")
        settings = _settings(retrieval_decompose_enabled=False)

        result = decompose_query(_COMPOUND_OU, settings)

        assert result == [_COMPOUND_OU]
        mock_cls.assert_not_called()

    def test_not_compound_returns_query_without_llm(self, mocker) -> None:
        mock_cls = mocker.patch("medasist.retrieval.decompose.ChatOpenAI")
        settings = _settings()

        result = decompose_query("Alphazol causa sonolência intensa", settings)

        assert result == ["Alphazol causa sonolência intensa"]
        mock_cls.assert_not_called()


# ---------------------------------------------------------------------------
# Testes — decompose_query (split)
# ---------------------------------------------------------------------------


class TestDecomposeSplit:
    def test_compound_splits_into_sub_questions(self, mocker) -> None:
        _mock_split_llm(mocker, "Qual a dose de Alphazol?\nPosso tomar com Betazol?")

        result = decompose_query(_COMPOUND_OU, _settings())

        assert result == ["Qual a dose de Alphazol?", "Posso tomar com Betazol?"]

    def test_two_plus_subs_parsed(self, mocker) -> None:
        _mock_split_llm(mocker, "s1\ns2\ns3")
        assert decompose_query(_COMPOUND_OU, _settings()) == ["s1", "s2", "s3"]

    def test_lines_stripped_and_empty_filtered(self, mocker) -> None:
        _mock_split_llm(mocker, "  s1  \n\n   \ns2")
        assert decompose_query(_COMPOUND_OU, _settings()) == ["s1", "s2"]

    def test_cap_respected(self, mocker) -> None:
        _mock_split_llm(mocker, "\n".join(f"sub {i}" for i in range(7)))
        settings = _settings(retrieval_decompose_max_sub_questions=5)

        result = decompose_query(_COMPOUND_OU, settings)

        assert len(result) == 5
        assert result == [f"sub {i}" for i in range(5)]

    def test_llm_constructed_with_resolved_model_and_temperature(self, mocker) -> None:
        mock_cls, _ = _mock_split_llm(mocker, "s1\ns2")
        settings = _settings(
            retrieval_decompose_model="split-mini",
            retrieval_decompose_temperature=0.0,
            retrieval_decompose_max_tokens=64,
        )

        decompose_query(_COMPOUND_OU, settings)

        call_kwargs = mock_cls.call_args.kwargs
        assert call_kwargs["model"] == "split-mini"
        assert call_kwargs["temperature"] == 0.0
        assert call_kwargs["max_tokens"] == 64
        assert call_kwargs["max_retries"] == settings.llm_max_retries
        assert call_kwargs["request_timeout"] == settings.llm_request_timeout

    def test_empty_model_resolves_to_lm_studio_llm_model(self, mocker) -> None:
        mock_cls, _ = _mock_split_llm(mocker, "s1\ns2")
        settings = _settings(
            lm_studio_llm_model="phi-3-mini",
            retrieval_decompose_model="",
        )

        decompose_query(_COMPOUND_OU, settings)

        assert mock_cls.call_args.kwargs["model"] == "phi-3-mini"


# ---------------------------------------------------------------------------
# Testes — decompose_query (degradação graciosa)
# ---------------------------------------------------------------------------


class TestDecomposeDegradation:
    def test_llm_failure_returns_original_and_logs(self, mocker, caplog) -> None:
        instance = MagicMock()
        instance.side_effect = RuntimeError("timeout do LM Studio")
        mocker.patch(
            "medasist.retrieval.decompose.ChatOpenAI", return_value=instance
        )
        settings = _settings()

        with caplog.at_level(
            logging.ERROR, logger="medasist.retrieval.decompose"
        ):
            result = decompose_query(_COMPOUND_OU, settings)

        assert result == [_COMPOUND_OU]
        assert any(
            r.levelno == logging.ERROR and "Decomposição falhou" in r.getMessage()
            for r in caplog.records
        )

    def test_empty_output_returns_original(self, mocker) -> None:
        _mock_split_llm(mocker, "   ")
        assert decompose_query(_COMPOUND_OU, _settings()) == [_COMPOUND_OU]

    def test_zero_sub_questions_returns_original(self, mocker) -> None:
        _mock_split_llm(mocker, "")
        assert decompose_query(_COMPOUND_OU, _settings()) == [_COMPOUND_OU]

    def test_single_sub_question_returns_original(self, mocker) -> None:
        _mock_split_llm(mocker, "Qual a dose de Alphazol?")
        assert decompose_query(_COMPOUND_OU, _settings()) == [_COMPOUND_OU]


# ---------------------------------------------------------------------------
# Testes — prompt de decomposição proíbe preâmbulo
# ---------------------------------------------------------------------------


class TestDecomposePrompt:
    def test_prompt_forbids_preamble_and_commentary(self) -> None:
        template = decompose._DECOMPOSE_PROMPT.template.lower()
        assert "preâmbulo" in template or "preambulo" in template
        assert "sem comentário" in template
        assert "{query}" in decompose._DECOMPOSE_PROMPT.template
