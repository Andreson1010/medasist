from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from langchain_core.documents import Document
from langchain_core.messages import AIMessage

from medasist.generation.chain import GenerationResult, _format_context, run_query
from medasist.generation.citations import CitationItem
from medasist.profiles.schemas import UserProfile


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_doc(content: str, source: str = "bula_x.pdf", page: str = "1") -> Document:
    return Document(
        page_content=content,
        metadata={"source": source, "section": "Posologia", "page": page},
    )


def _make_settings(
    cold_start_message: str = "Não encontrei essa informação.",
    disclaimer: str = "Este sistema é auxiliar informativo.",
) -> MagicMock:
    settings = MagicMock()
    settings.cold_start_message = cold_start_message
    settings.disclaimer = disclaimer
    settings.lm_studio_base_url = "http://localhost:1234/v1"
    settings.lm_studio_api_key = MagicMock()
    settings.lm_studio_api_key.get_secret_value.return_value = "lm-studio"
    settings.lm_studio_llm_model = "phi-3-mini"
    settings.retrieval_top_k = 5
    settings.retrieval_score_threshold = 0.4
    return settings


# ---------------------------------------------------------------------------
# _format_context
# ---------------------------------------------------------------------------


class TestFormatContext:
    def test_numbers_docs_starting_at_one(self) -> None:
        docs = [_make_doc("texto A"), _make_doc("texto B")]
        result = _format_context(docs)
        assert "[1] texto A" in result
        assert "[2] texto B" in result

    def test_single_doc(self) -> None:
        docs = [_make_doc("único")]
        assert _format_context(docs) == "[1] único"

    def test_empty_docs_returns_empty_string(self) -> None:
        assert _format_context([]) == ""


# ---------------------------------------------------------------------------
# GenerationResult — imutabilidade
# ---------------------------------------------------------------------------


class TestGenerationResult:
    def test_is_immutable(self) -> None:
        result = GenerationResult(
            answer="resposta",
            citations=[],
            profile=UserProfile.MEDICO,
            disclaimer="aviso",
            is_cold_start=False,
        )
        with pytest.raises((AttributeError, TypeError)):
            result.answer = "outro"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# run_query — cold start
# ---------------------------------------------------------------------------


class TestRunQueryColdStart:
    def test_returns_cold_start_when_no_docs(self) -> None:
        settings = _make_settings(cold_start_message="Sem informação.")
        stores = MagicMock()

        with patch(
            "medasist.generation.chain.build_retriever"
        ) as mock_retriever_builder:
            mock_retriever = MagicMock()
            mock_retriever.invoke.return_value = []
            mock_retriever_builder.return_value = mock_retriever

            result = run_query(
                question="qual a dose?",
                stores=stores,
                profile=UserProfile.MEDICO,
                settings=settings,
            )

        assert result.is_cold_start is True
        assert result.answer == "Sem informação."
        assert result.citations == []

    def test_disclaimer_present_on_cold_start(self) -> None:
        settings = _make_settings(disclaimer="Aviso médico.")
        stores = MagicMock()

        with patch("medasist.generation.chain.build_retriever") as mock_rb:
            mock_retriever = MagicMock()
            mock_retriever.invoke.return_value = []
            mock_rb.return_value = mock_retriever

            result = run_query("q?", stores, UserProfile.PACIENTE, settings)

        assert result.disclaimer == "Aviso médico."

    def test_llm_not_called_on_cold_start(self) -> None:
        settings = _make_settings()
        stores = MagicMock()

        with (
            patch("medasist.generation.chain.build_retriever") as mock_rb,
            patch("medasist.generation.chain.ChatOpenAI") as mock_llm_cls,
        ):
            mock_retriever = MagicMock()
            mock_retriever.invoke.return_value = []
            mock_rb.return_value = mock_retriever

            run_query("q?", stores, UserProfile.MEDICO, settings)

        mock_llm_cls.assert_not_called()


# ---------------------------------------------------------------------------
# run_query — caminho normal
# ---------------------------------------------------------------------------


class TestRunQueryNormal:
    def _run_with_mock_llm(
        self,
        docs: list[Document],
        llm_response: str,
        profile: UserProfile = UserProfile.MEDICO,
    ) -> GenerationResult:
        settings = _make_settings()
        stores = MagicMock()

        with (
            patch("medasist.generation.chain.build_retriever") as mock_rb,
            patch("medasist.generation.chain.ChatOpenAI") as mock_llm_cls,
        ):
            mock_retriever = MagicMock()
            mock_retriever.invoke.return_value = docs
            mock_rb.return_value = mock_retriever

            mock_llm_instance = MagicMock()
            mock_llm_cls.return_value = mock_llm_instance

            # LangChain trata MagicMock como callable (RunnableLambda).
            # O StrOutputParser espera AIMessage — setar return_value garante isso.
            mock_llm_instance.return_value = AIMessage(content=llm_response)

            return run_query("qual a dose?", stores, profile, settings)

    def test_is_not_cold_start_when_docs_exist(self) -> None:
        docs = [_make_doc("texto relevante [1]")]
        result = self._run_with_mock_llm(docs, "Resposta com [1].")
        assert result.is_cold_start is False

    def test_disclaimer_always_present(self) -> None:
        docs = [_make_doc("texto")]
        result = self._run_with_mock_llm(docs, "Resposta [1].")
        assert result.disclaimer == "Este sistema é auxiliar informativo."

    def test_profile_preserved_in_result(self) -> None:
        docs = [_make_doc("texto")]
        result = self._run_with_mock_llm(
            docs, "Resposta [1].", profile=UserProfile.ENFERMEIRO
        )
        assert result.profile == UserProfile.ENFERMEIRO

    def test_citations_extracted_from_docs(self) -> None:
        docs = [_make_doc("texto A", source="bula_a.pdf")]
        result = self._run_with_mock_llm(docs, "Veja [1] para mais detalhes.")
        assert len(result.citations) == 1
        assert isinstance(result.citations[0], CitationItem)
        assert result.citations[0].source == "bula_a.pdf"

    def test_orphan_citations_removed(self) -> None:
        docs = [_make_doc("A", source="a.pdf"), _make_doc("B", source="b.pdf")]
        # LLM usa apenas [1], não [2]
        result = self._run_with_mock_llm(docs, "Apenas [1] é relevante.")
        assert len(result.citations) == 1
        assert result.citations[0].index == 1

    def test_llm_response_without_citations_triggers_cold_start(self) -> None:
        """Regra médica inegociável: resposta sem citações → cold start."""
        docs = [_make_doc("texto A", source="bula_a.pdf")]
        # LLM produz resposta sem nenhum marcador [N]
        result = self._run_with_mock_llm(docs, "Resposta sem marcadores de citação.")
        assert result.is_cold_start is True
        assert result.citations == []

    def test_hallucinated_citation_marker_triggers_cold_start(self) -> None:
        """Marcador [N] alucinado (N > nº de docs) → cold start."""
        docs = [_make_doc("texto A", source="bula_a.pdf")]
        # LLM cita [99] que não existe entre os documentos recuperados
        result = self._run_with_mock_llm(docs, "Veja [99] para mais detalhes.")
        assert result.is_cold_start is True
        assert result.citations == []
