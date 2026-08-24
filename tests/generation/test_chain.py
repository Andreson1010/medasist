from __future__ import annotations

import re
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.documents import Document
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

from medasist.generation.chain import (
    GenerationResult,
    _format_context,
    build_stream_chain,
    run_query,
    stream_answer,
)
from medasist.generation.citations import CitationItem
from medasist.ingestion.schemas import DocType
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
    settings.llm_max_retries = 2
    settings.llm_request_timeout = 60.0
    settings.retrieval_decompose_enabled = False
    return settings


def _make_decompose_settings(**overrides: object) -> MagicMock:
    """Settings com decomposição habilitada e overrides por critério."""
    settings = _make_settings()
    settings.retrieval_decompose_enabled = True
    settings.retrieval_decompose_max_sub_questions = 5
    settings.retrieval_decompose_model = "phi-3-mini"
    settings.retrieval_decompose_temperature = 0.0
    settings.retrieval_decompose_max_tokens = 256
    settings.retrieval_decompose_min_tokens = 4
    for key, value in overrides.items():
        setattr(settings, key, value)
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
# run_query — filtragem de doc_types
# ---------------------------------------------------------------------------


def _make_mock_stores() -> dict[DocType, MagicMock]:
    return {dt: MagicMock(name=f"store_{dt.value}") for dt in DocType}


class TestRunQueryDocTypeFiltering:
    def _call_with(
        self,
        stores: dict,
        doc_types: list[DocType] | None,
    ) -> MagicMock:
        settings = _make_settings()

        with patch("medasist.generation.chain.build_retriever") as mock_rb:
            mock_retriever = MagicMock()
            mock_retriever.invoke.return_value = []
            mock_rb.return_value = mock_retriever

            run_query(
                question="qual a dose?",
                stores=stores,
                profile=UserProfile.MEDICO,
                settings=settings,
                doc_types=doc_types,
            )

        return mock_rb

    def test_doc_types_subset_passed_to_build_retriever(self) -> None:
        stores = _make_mock_stores()
        requested = [DocType.BULA, DocType.PROTOCOLO]

        mock_rb = self._call_with(stores, requested)

        delivered = mock_rb.call_args.args[0]
        assert set(delivered.keys()) == set(requested)

    def test_missing_doc_type_key_ignored(self) -> None:
        stores = _make_mock_stores()
        requested = [DocType.BULA, "NAO_EXISTE"]

        mock_rb = self._call_with(stores, requested)  # type: ignore[list-item]

        delivered = mock_rb.call_args.args[0]
        assert set(delivered.keys()) == {DocType.BULA}

    def test_none_passes_full_stores(self) -> None:
        stores = _make_mock_stores()

        mock_rb = self._call_with(stores, None)

        assert mock_rb.call_args.args[0] is stores
        assert set(mock_rb.call_args.args[0].keys()) == set(stores.keys())

    def test_empty_list_passes_full_stores(self) -> None:
        stores = _make_mock_stores()

        mock_rb = self._call_with(stores, [])

        assert mock_rb.call_args.args[0] is stores

    def test_omitted_parameter_passes_full_stores(self) -> None:
        stores = _make_mock_stores()
        settings = _make_settings()

        with patch("medasist.generation.chain.build_retriever") as mock_rb:
            mock_retriever = MagicMock()
            mock_retriever.invoke.return_value = []
            mock_rb.return_value = mock_retriever

            run_query(
                question="qual a dose?",
                stores=stores,
                profile=UserProfile.MEDICO,
                settings=settings,
            )

        assert mock_rb.call_args.args[0] is stores

    def test_original_stores_never_mutated(self) -> None:
        stores = _make_mock_stores()
        original_keys = set(stores.keys())

        self._call_with(stores, [DocType.BULA])

        assert set(stores.keys()) == original_keys


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

    def test_rerank_enabled_contract_intact_and_citations_valid(self) -> None:
        """RAG-01: com rerank habilitado, run_query mantém contrato e citações.

        O rerank ocorre dentro do retriever (mockado aqui); o que importa para
        a chain é que documentos rerankados (qualquer ordem) gerem citações [N]
        mapeando para CitationItem válidos e resultado não-cold-start.
        """
        settings = _make_settings()
        settings.retrieval_rerank_enabled = True
        stores = MagicMock()

        # docs em ordem rerankada (mais relevante primeiro)
        docs = [
            _make_doc("texto B", source="bula_b.pdf"),
            _make_doc("texto A", source="bula_a.pdf"),
        ]

        with (
            patch("medasist.generation.chain.build_retriever") as mock_rb,
            patch("medasist.generation.chain.ChatOpenAI") as mock_llm_cls,
        ):
            mock_retriever = MagicMock()
            mock_retriever.invoke.return_value = docs
            mock_rb.return_value = mock_retriever
            mock_llm_instance = MagicMock()
            mock_llm_cls.return_value = mock_llm_instance
            mock_llm_instance.return_value = AIMessage(content="Recomendo [1] e [2].")

            result = run_query("qual a dose?", stores, UserProfile.MEDICO, settings)

        assert result.is_cold_start is False
        assert len(result.citations) == 2
        assert isinstance(result.citations[0], CitationItem)
        assert isinstance(result.citations[1], CitationItem)
        assert {c.index for c in result.citations} == {1, 2}
        assert result.citations[0].source == "bula_b.pdf"
        assert result.citations[1].source == "bula_a.pdf"

    def test_chatopenai_receives_retry_and_timeout_from_settings(self) -> None:
        """OBS-04: retry/backoff e timeout do Settings chegam ao ChatOpenAI."""
        settings = _make_settings()
        settings.llm_max_retries = 4
        settings.llm_request_timeout = 90.0
        settings.medico_temperature = 0.1
        settings.medico_max_tokens = 1024
        stores = MagicMock()

        with (
            patch("medasist.generation.chain.build_retriever") as mock_rb,
            patch("medasist.generation.chain.ChatOpenAI") as mock_llm_cls,
        ):
            mock_retriever = MagicMock()
            mock_retriever.invoke.return_value = [_make_doc("texto relevante [1]")]
            mock_rb.return_value = mock_retriever

            mock_llm_instance = MagicMock()
            mock_llm_cls.return_value = mock_llm_instance
            mock_llm_instance.return_value = AIMessage(content="Resposta com [1].")

            run_query("qual a dose?", stores, UserProfile.MEDICO, settings)

        mock_llm_cls.assert_called_once_with(
            base_url="http://localhost:1234/v1",
            api_key="lm-studio",
            model="phi-3-mini",
            temperature=0.1,
            max_tokens=1024,
            max_retries=4,
            request_timeout=90.0,
        )


# ---------------------------------------------------------------------------
# run_query — decomposição multi-parte (RAG-03)
# ---------------------------------------------------------------------------


def _result(
    answer: str = "resposta",
    citations: list[CitationItem] | None = None,
    is_cold_start: bool = False,
) -> GenerationResult:
    return GenerationResult(
        answer=answer,
        citations=citations or [],
        profile=UserProfile.MEDICO,
        disclaimer="d",
        is_cold_start=is_cold_start,
    )


class TestRunQueryDecompose:
    def test_flag_off_identity_single_run(self) -> None:
        settings = _make_settings()  # decomposição desabilitada
        stores = MagicMock()
        expected = _result(answer="r", citations=[CitationItem(1, "a.pdf", "S", "1")])

        with (
            patch("medasist.retrieval.decompose.ChatOpenAI") as mock_split,
            patch("medasist.generation.chain._run_single") as mock_single,
        ):
            mock_single.return_value = expected
            result = run_query("qual a dose?", stores, UserProfile.MEDICO, settings)

        mock_split.assert_not_called()
        mock_single.assert_called_once_with(
            "qual a dose?", stores, UserProfile.MEDICO, settings, None
        )
        assert result == expected

    def test_single_sub_identity(self) -> None:
        settings = _make_decompose_settings()
        stores = MagicMock()
        expected = _result()

        with (
            patch(
                "medasist.generation.chain.decompose_query",
                return_value=["qual a dose?"],
            ),
            patch("medasist.generation.chain._run_single") as mock_single,
        ):
            mock_single.return_value = expected
            result = run_query("qual a dose?", stores, UserProfile.MEDICO, settings)

        assert result == expected
        mock_single.assert_called_once_with(
            "qual a dose?", stores, UserProfile.MEDICO, settings, None
        )

    def test_merge_renumbers_and_remaps(self) -> None:
        settings = _make_decompose_settings()
        stores = MagicMock()
        subs = ["Qual a dose de Alphazol?", "Posso tomar Alphazol com Betazol?"]

        with (
            patch("medasist.generation.chain.decompose_query", return_value=subs),
            patch(
                "medasist.generation.chain._run_single",
                side_effect=[
                    _result(
                        "Dose: 500 mg [1].",
                        [CitationItem(1, "bula_a.pdf", "Posologia", "1")],
                    ),
                    _result(
                        "Evite [1] e [2].",
                        [
                            CitationItem(1, "bula_b.pdf", "Interação", "2"),
                            CitationItem(2, "bula_c.pdf", "Advertência", "3"),
                        ],
                    ),
                ],
            ),
        ):
            result = run_query("composta?", stores, UserProfile.MEDICO, settings)

        assert result.is_cold_start is False
        assert result.answer == "Dose: 500 mg [1].\n\nEvite [2] e [3]."
        assert [c.index for c in result.citations] == [1, 2, 3]
        assert result.citations[0].source == "bula_a.pdf"
        assert result.citations[1].source == "bula_b.pdf"
        assert result.citations[2].source == "bula_c.pdf"
        assert result.unanswered_sub_questions == []

    def test_merge_non_contiguous_citations_no_collision(self) -> None:
        """RAG-03 fix: sub com citações NÃO-contíguas não colide no merge.

        sub1 cita ``[1]`` e ``[3]`` (``[2]`` não referenciada — validate_citations
        preserva os índices ORIGINAIS, deixando o conjunto {1, 3}); sub2 cita
        ``[1]`` e ``[2]``. Com o deslocamento linear por ``len`` (antigo), sub2
        viraria ``[3]`` e ``[4]`` colidindo com o ``[3]`` de sub1. A re-numeração
        SEQUENCIAL via mapa produz índices únicos 1..M.
        """
        settings = _make_decompose_settings()
        stores = MagicMock()
        subs = ["s1", "s2"]

        with (
            patch("medasist.generation.chain.decompose_query", return_value=subs),
            patch(
                "medasist.generation.chain._run_single",
                side_effect=[
                    _result(
                        "Dose A [1] e interação B [3].",
                        [
                            CitationItem(1, "a.pdf", "Posologia", "1"),
                            CitationItem(3, "b.pdf", "Interação", "2"),
                        ],
                    ),
                    _result(
                        "Evite [1] e [2].",
                        [
                            CitationItem(1, "c.pdf", "Advertência", "3"),
                            CitationItem(2, "d.pdf", "Advertência", "4"),
                        ],
                    ),
                ],
            ),
        ):
            result = run_query("composta?", stores, UserProfile.MEDICO, settings)

        assert result.is_cold_start is False
        assert result.answer == ("Dose A [1] e interação B [2].\n\nEvite [3] e [4].")
        # índices únicos 1..M, sem colisão (regra médica 1:1 citação↔fonte)
        assert [c.index for c in result.citations] == [1, 2, 3, 4]
        assert len({c.index for c in result.citations}) == 4
        assert result.citations[0].source == "a.pdf"
        assert result.citations[1].source == "b.pdf"
        assert result.citations[2].source == "c.pdf"
        assert result.citations[3].source == "d.pdf"
        # todo [N] do merged tem CitationItem correspondente (1:1)
        markers = {int(m) for m in re.findall(r"\[(\d+)\]", result.answer)}
        assert markers == {c.index for c in result.citations}

    def test_some_miss_unanswered(self) -> None:
        settings = _make_decompose_settings()
        stores = MagicMock()
        subs = ["s1", "s2", "s3"]

        with (
            patch("medasist.generation.chain.decompose_query", return_value=subs),
            patch(
                "medasist.generation.chain._run_single",
                side_effect=[
                    _result("A [1].", [CitationItem(1, "a.pdf", "S", "1")]),
                    _result(is_cold_start=True),
                    _result("C [1].", [CitationItem(1, "c.pdf", "S", "1")]),
                ],
            ),
        ):
            result = run_query("q?", stores, UserProfile.MEDICO, settings)

        assert result.is_cold_start is False
        assert result.unanswered_sub_questions == ["s2"]
        assert result.answer == "A [1].\n\nC [2]."
        assert [c.index for c in result.citations] == [1, 2]

    def test_all_miss_cold_start_total(self) -> None:
        settings = _make_decompose_settings()
        stores = MagicMock()
        subs = ["s1", "s2"]

        with (
            patch("medasist.generation.chain.decompose_query", return_value=subs),
            patch(
                "medasist.generation.chain._run_single",
                side_effect=[_result(is_cold_start=True), _result(is_cold_start=True)],
            ),
        ):
            result = run_query("q?", stores, UserProfile.MEDICO, settings)

        assert result.is_cold_start is True
        assert result.answer == settings.cold_start_message
        assert result.citations == []
        assert result.unanswered_sub_questions == []

    def test_sub_without_valid_citation_is_miss(self) -> None:
        settings = _make_decompose_settings()
        stores = MagicMock()
        subs = ["s1", "s2"]

        with (
            patch("medasist.generation.chain.decompose_query", return_value=subs),
            patch(
                "medasist.generation.chain._run_single",
                side_effect=[
                    _result("A [1].", [CitationItem(1, "a.pdf", "S", "1")]),
                    _result("sem citação"),
                ],
            ),
        ):
            result = run_query("q?", stores, UserProfile.MEDICO, settings)

        assert result.is_cold_start is False
        assert result.unanswered_sub_questions == ["s2"]
        assert result.answer == "A [1]."
        assert [c.index for c in result.citations] == [1]

    def test_each_sub_passes_funnel(self) -> None:
        settings = _make_decompose_settings()
        stores = MagicMock()
        question = "Qual a dose de Alphazol ou posso tomar com Betazol?"
        subs = ["Qual a dose de Alphazol?", "Posso tomar Alphazol com Betazol?"]
        split_instance = MagicMock()
        split_instance.return_value = AIMessage(content="\n".join(subs))
        invoked: list[str] = []

        def _builder(stores, settings):
            retriever = MagicMock()

            def _invoke(q):
                invoked.append(q)
                return [_make_doc("texto [1]")]

            retriever.invoke.side_effect = _invoke
            return retriever

        gen_instance = MagicMock()
        gen_instance.return_value = AIMessage(content="Resposta [1].")

        with (
            patch(
                "medasist.retrieval.decompose.ChatOpenAI",
                return_value=split_instance,
            ),
            patch("medasist.generation.chain.build_retriever", side_effect=_builder),
            patch("medasist.generation.chain.ChatOpenAI", return_value=gen_instance),
        ):
            result = run_query(question, stores, UserProfile.MEDICO, settings)

        assert invoked == subs
        assert result.is_cold_start is False
        assert [c.index for c in result.citations] == [1, 2]

    def test_cap_respected(self) -> None:
        settings = _make_decompose_settings(retrieval_decompose_max_sub_questions=5)
        stores = MagicMock()
        question = "Qual a dose de Alphazol ou posso tomar com Betazol?"
        split_instance = MagicMock()
        split_instance.return_value = AIMessage(
            content="\n".join(f"sub {i}" for i in range(7))
        )

        with (
            patch(
                "medasist.retrieval.decompose.ChatOpenAI",
                return_value=split_instance,
            ),
            patch(
                "medasist.generation.chain._run_single",
                return_value=_result(is_cold_start=True),
            ) as mock_single,
        ):
            run_query(question, stores, UserProfile.MEDICO, settings)

        assert mock_single.call_count == 5

    def test_sub_questions_only_as_each_sub_question(self) -> None:
        settings = _make_decompose_settings()
        stores = MagicMock()
        subs = ["sub A", "sub B"]

        with (
            patch("medasist.generation.chain.decompose_query", return_value=subs),
            patch(
                "medasist.generation.chain._run_single",
                side_effect=[
                    _result("A [1].", [CitationItem(1, "a.pdf", "S", "1")]),
                    _result("B [1].", [CitationItem(1, "b.pdf", "S", "1")]),
                ],
            ) as mock_single,
        ):
            run_query("composta?", stores, UserProfile.MEDICO, settings)

        questions = [call.args[0] for call in mock_single.call_args_list]
        assert questions == ["sub A", "sub B"]

    def test_empty_stores_short_circuits_before_split(self) -> None:
        """Edge case RAG-03: stores vazio → cold start ANTES de qualquer split.

        Com ``retrieval_decompose_enabled=True`` e pergunta composta, o LLM de
        split nunca é chamado quando nenhuma coleção é selecionada.
        """
        settings = _make_decompose_settings()
        stores: dict[Any, Any] = {}
        question = "Qual a dose de Alphazol e posso tomar com Betazol?"

        with (
            patch("medasist.generation.chain.decompose_query") as mock_decompose,
            patch("medasist.retrieval.decompose.ChatOpenAI") as mock_split,
            patch("medasist.generation.chain.ChatOpenAI") as mock_llm_cls,
        ):
            result = run_query(question, stores, UserProfile.MEDICO, settings)

        mock_decompose.assert_not_called()
        mock_split.assert_not_called()
        mock_llm_cls.assert_not_called()
        assert result.is_cold_start is True
        assert result.answer == settings.cold_start_message
        assert result.citations == []
        assert result.disclaimer == settings.disclaimer

    def test_doc_types_filtering_all_short_circuits_before_split(self) -> None:
        """Edge case RAG-03: doc_types que filtram tudo → cold start sem split."""
        settings = _make_decompose_settings()
        stores = {DocType.BULA: MagicMock(name="store_bula")}
        question = "Qual a dose de Alphazol e posso tomar com Betazol?"

        with (
            patch("medasist.generation.chain.decompose_query") as mock_decompose,
            patch("medasist.retrieval.decompose.ChatOpenAI") as mock_split,
        ):
            result = run_query(
                question,
                stores,
                UserProfile.MEDICO,
                settings,
                doc_types=[DocType.PROTOCOLO],
            )

        mock_decompose.assert_not_called()
        mock_split.assert_not_called()
        assert result.is_cold_start is True
        assert result.answer == settings.cold_start_message
        assert result.citations == []


# ---------------------------------------------------------------------------
# stream_answer
# ---------------------------------------------------------------------------


def _make_stream_llm(*deltas: str) -> RunnableLambda:
    """LLM fake que ``stream`` yield os deltas informados.

    ``RunnableLambda`` envolvendo um gerador funciona corretamente na
    composição ``prompt | llm | StrOutputParser`` ao chamar ``.stream``.
    """

    def fake_stream(input, config=None, **kwargs):  # type: ignore[no-untyped-def]
        yield from deltas

    return RunnableLambda(fake_stream)


def _consume(gen: Any) -> tuple[list[str], tuple[list[CitationItem], bool]]:
    """Consome um gerador de ``stream_answer`` até o estado terminal.

    Returns
    -------
    tuple[list[str], tuple[list[CitationItem], bool]]
        ``(deltas, terminal)`` onde ``terminal`` é o valor de retorno do gerador.
    """
    deltas: list[str] = []
    terminal = ([], True)
    while True:
        try:
            deltas.append(next(gen))
        except StopIteration as stop:
            terminal = stop.value
            break
    return deltas, terminal


# ---------------------------------------------------------------------------
# stream_answer — decomposição multi-parte (RAG-03)
# ---------------------------------------------------------------------------


def _stream_result(
    *deltas: str,
    full_answer: str,
    citations: list[CitationItem],
    is_cold_start: bool,
) -> Any:
    """Gerador fake de ``_stream_single`` que yield deltas e retorna o terminal."""

    def gen() -> Any:
        yield from deltas
        return full_answer, citations, is_cold_start

    return gen()


class TestStreamDecompose:
    def test_deltas_concatenate_to_merged(self) -> None:
        settings = _make_decompose_settings()
        stores = MagicMock()
        subs = ["s1", "s2"]
        results = [
            _stream_result(full_answer="", citations=[], is_cold_start=True),
            _stream_result(
                "B ",
                "[1].",
                full_answer="B [1].",
                citations=[CitationItem(1, "b.pdf", "S", "1")],
                is_cold_start=False,
            ),
        ]

        with (
            patch("medasist.generation.chain.decompose_query", return_value=subs),
            patch("medasist.generation.chain._stream_single", side_effect=results),
        ):
            gen = stream_answer("q?", stores, UserProfile.MEDICO, settings)
            deltas, (citations, is_cold_start) = _consume(gen)

        assert "".join(deltas) == "B [1]."
        assert is_cold_start is False
        assert [c.index for c in citations] == [1]

    def test_two_hits_renumbered(self) -> None:
        settings = _make_decompose_settings()
        stores = MagicMock()
        subs = ["s1", "s2"]
        results = [
            _stream_result(
                "A ",
                "[1].",
                full_answer="A [1].",
                citations=[CitationItem(1, "a.pdf", "S", "1")],
                is_cold_start=False,
            ),
            _stream_result(
                "B ",
                "[1].",
                full_answer="B [1].",
                citations=[CitationItem(1, "b.pdf", "S", "1")],
                is_cold_start=False,
            ),
        ]

        with (
            patch("medasist.generation.chain.decompose_query", return_value=subs),
            patch("medasist.generation.chain._stream_single", side_effect=results),
        ):
            gen = stream_answer("q?", stores, UserProfile.MEDICO, settings)
            deltas, (citations, is_cold_start) = _consume(gen)

        # deltas concatenados = merged síncrono (separador + remap de [N])
        assert "".join(deltas) == "A [1].\n\nB [2]."
        assert is_cold_start is False
        # citações re-numeradas no espaço 1-based único
        assert [c.index for c in citations] == [1, 2]

    def test_non_contiguous_citations_no_collision(self) -> None:
        """RAG-03 fix: streaming com sub NÃO-contígua → índices únicos 1..M.

        sub1 cita ``[1]`` e ``[3]`` (``[2]`` não referenciada) e sub2 cita
        ``[1]`` e ``[2]``; o mapa sequencial renumera para 1..4 sem colisão,
        com paridade com o merged síncrono.
        """
        settings = _make_decompose_settings()
        stores = MagicMock()
        subs = ["s1", "s2"]
        results = [
            _stream_result(
                "Dose A ",
                "[1] e interação B [3].",
                full_answer="Dose A [1] e interação B [3].",
                citations=[
                    CitationItem(1, "a.pdf", "S", "1"),
                    CitationItem(3, "b.pdf", "S", "2"),
                ],
                is_cold_start=False,
            ),
            _stream_result(
                "Evite ",
                "[1] e [2].",
                full_answer="Evite [1] e [2].",
                citations=[
                    CitationItem(1, "c.pdf", "S", "3"),
                    CitationItem(2, "d.pdf", "S", "4"),
                ],
                is_cold_start=False,
            ),
        ]

        with (
            patch("medasist.generation.chain.decompose_query", return_value=subs),
            patch("medasist.generation.chain._stream_single", side_effect=results),
        ):
            gen = stream_answer("q?", stores, UserProfile.MEDICO, settings)
            deltas, (citations, is_cold_start) = _consume(gen)

        assert "".join(deltas) == ("Dose A [1] e interação B [2].\n\nEvite [3] e [4].")
        assert is_cold_start is False
        # índices únicos 1..M, sem colisão (paridade com o sync)
        assert [c.index for c in citations] == [1, 2, 3, 4]
        assert len({c.index for c in citations}) == 4

    def test_partial_cold_start(self) -> None:
        settings = _make_decompose_settings()
        stores = MagicMock()
        subs = ["s1", "s2"]
        results = [
            _stream_result(
                "A ",
                "[1].",
                full_answer="A [1].",
                citations=[CitationItem(1, "a.pdf", "S", "1")],
                is_cold_start=False,
            ),
            _stream_result(full_answer="", citations=[], is_cold_start=True),
        ]

        with (
            patch("medasist.generation.chain.decompose_query", return_value=subs),
            patch("medasist.generation.chain._stream_single", side_effect=results),
        ):
            gen = stream_answer("q?", stores, UserProfile.MEDICO, settings)
            deltas, (citations, is_cold_start) = _consume(gen)

        assert "".join(deltas) == "A [1]."
        assert is_cold_start is False
        assert [c.index for c in citations] == [1]

    def test_all_miss_cold_start(self) -> None:
        settings = _make_decompose_settings()
        stores = MagicMock()
        subs = ["s1", "s2"]
        results = [
            _stream_result(full_answer="", citations=[], is_cold_start=True),
            _stream_result(full_answer="", citations=[], is_cold_start=True),
        ]

        with (
            patch("medasist.generation.chain.decompose_query", return_value=subs),
            patch("medasist.generation.chain._stream_single", side_effect=results),
        ):
            gen = stream_answer("q?", stores, UserProfile.MEDICO, settings)
            deltas, (citations, is_cold_start) = _consume(gen)

        assert deltas == []
        assert (citations, is_cold_start) == ([], True)

    def test_flag_off_identity(self) -> None:
        settings = _make_settings()  # decomposição desabilitada
        stores = MagicMock()
        result = _stream_result(
            "Olá ",
            "mundo [1].",
            full_answer="Olá mundo [1].",
            citations=[CitationItem(1, "a.pdf", "S", "1")],
            is_cold_start=False,
        )

        with (
            patch("medasist.retrieval.decompose.ChatOpenAI") as mock_split,
            patch(
                "medasist.generation.chain._stream_single", return_value=result
            ) as mock_single,
        ):
            gen = stream_answer("qual a dose?", stores, UserProfile.MEDICO, settings)
            deltas, (citations, is_cold_start) = _consume(gen)

        mock_split.assert_not_called()
        mock_single.assert_called_once_with(
            "qual a dose?", stores, UserProfile.MEDICO, settings, None
        )
        assert "".join(deltas) == "Olá mundo [1]."
        assert is_cold_start is False
        assert [c.index for c in citations] == [1]

    def test_empty_stores_short_circuits_before_split(self) -> None:
        """Edge case RAG-03: stores vazio → cold start ANTES do split (stream)."""
        settings = _make_decompose_settings()
        stores: dict[Any, Any] = {}
        question = "Qual a dose de Alphazol e posso tomar com Betazol?"

        with (
            patch("medasist.generation.chain.decompose_query") as mock_decompose,
            patch("medasist.retrieval.decompose.ChatOpenAI") as mock_split,
            patch("medasist.generation.chain.ChatOpenAI") as mock_llm_cls,
        ):
            gen = stream_answer(question, stores, UserProfile.MEDICO, settings)
            deltas, terminal = _consume(gen)

        mock_decompose.assert_not_called()
        mock_split.assert_not_called()
        mock_llm_cls.assert_not_called()
        assert deltas == []
        assert terminal == ([], True)

    def test_doc_types_filtering_all_short_circuits_before_split(self) -> None:
        """Edge case RAG-03: doc_types filtram tudo → cold start sem split."""
        settings = _make_decompose_settings()
        stores = {DocType.BULA: MagicMock(name="store_bula")}
        question = "Qual a dose de Alphazol e posso tomar com Betazol?"

        with (
            patch("medasist.generation.chain.decompose_query") as mock_decompose,
            patch("medasist.retrieval.decompose.ChatOpenAI") as mock_split,
        ):
            gen = stream_answer(
                question,
                stores,
                UserProfile.MEDICO,
                settings,
                doc_types=[DocType.PROTOCOLO],
            )
            deltas, terminal = _consume(gen)

        mock_decompose.assert_not_called()
        mock_split.assert_not_called()
        assert deltas == []
        assert terminal == ([], True)


class TestStreamAnswer:
    def test_deltas_concatenate_to_answer(self) -> None:
        settings = _make_settings()
        stores = MagicMock()

        with (
            patch("medasist.generation.chain.build_retriever") as mock_rb,
            patch("medasist.generation.chain.ChatOpenAI") as mock_llm_cls,
        ):
            mock_retriever = MagicMock()
            mock_retriever.invoke.return_value = [_make_doc("texto relevante")]
            mock_rb.return_value = mock_retriever
            mock_llm_cls.return_value = _make_stream_llm("Olá", " ", "mundo [1].")

            gen = stream_answer("qual a dose?", stores, UserProfile.MEDICO, settings)
            deltas, (citations, is_cold_start) = _consume(gen)

        assert "".join(deltas) == "Olá mundo [1]."
        assert is_cold_start is False
        assert len(citations) == 1
        assert isinstance(citations[0], CitationItem)

    def test_profile_respected_in_chatopenai_params(self) -> None:
        settings = _make_settings()
        settings.enfermeiro_temperature = 0.15
        settings.enfermeiro_max_tokens = 1024
        stores = MagicMock()

        with (
            patch("medasist.generation.chain.build_retriever") as mock_rb,
            patch("medasist.generation.chain.ChatOpenAI") as mock_llm_cls,
        ):
            mock_retriever = MagicMock()
            mock_retriever.invoke.return_value = [_make_doc("texto [1]")]
            mock_rb.return_value = mock_retriever
            mock_llm_cls.return_value = _make_stream_llm("Resposta [1].")

            gen = stream_answer(
                "qual a dose?", stores, UserProfile.ENFERMEIRO, settings
            )
            _consume(gen)

        mock_llm_cls.assert_called_once_with(
            base_url="http://localhost:1234/v1",
            api_key="lm-studio",
            model="phi-3-mini",
            temperature=0.15,
            max_tokens=1024,
            max_retries=2,
            request_timeout=60.0,
        )

    def test_cold_start_no_deltas_and_no_llm(self) -> None:
        settings = _make_settings()
        stores = MagicMock()

        with (
            patch("medasist.generation.chain.build_retriever") as mock_rb,
            patch("medasist.generation.chain.ChatOpenAI") as mock_llm_cls,
        ):
            mock_retriever = MagicMock()
            mock_retriever.invoke.return_value = []
            mock_rb.return_value = mock_retriever

            gen = stream_answer("qual a dose?", stores, UserProfile.MEDICO, settings)
            deltas, terminal = _consume(gen)

        assert deltas == []
        assert terminal == ([], True)
        mock_llm_cls.assert_not_called()

    def test_no_valid_citations_returns_cold_start(self) -> None:
        settings = _make_settings()
        stores = MagicMock()

        with (
            patch("medasist.generation.chain.build_retriever") as mock_rb,
            patch("medasist.generation.chain.ChatOpenAI") as mock_llm_cls,
        ):
            mock_retriever = MagicMock()
            mock_retriever.invoke.return_value = [_make_doc("texto [1]")]
            mock_rb.return_value = mock_retriever
            mock_llm_cls.return_value = _make_stream_llm("Resposta sem marcador.")

            gen = stream_answer("qual a dose?", stores, UserProfile.MEDICO, settings)
            deltas, terminal = _consume(gen)

        assert deltas == ["Resposta sem marcador."]
        assert terminal == ([], True)

    def test_doc_types_passed_to_select_collections(self) -> None:
        settings = _make_settings()
        stores = MagicMock()

        with (
            patch("medasist.generation.chain.select_collections") as mock_sel,
            patch("medasist.generation.chain.build_retriever") as mock_rb,
        ):
            mock_retriever = MagicMock()
            mock_retriever.invoke.return_value = []
            mock_rb.return_value = mock_retriever
            mock_sel.return_value = {}

            gen = stream_answer(
                "qual a dose?",
                stores,
                UserProfile.MEDICO,
                settings,
                doc_types=[DocType.BULA, DocType.PROTOCOLO],
            )
            _consume(gen)

        mock_sel.assert_called_once_with(stores, [DocType.BULA, DocType.PROTOCOLO])

    def test_stream_exception_propagates(self) -> None:
        settings = _make_settings()
        stores = MagicMock()

        def boom(input, config=None, **kwargs):  # type: ignore[no-untyped-def]
            raise RuntimeError("LM Studio indisponível")
            yield  # pragma: no cover

        with (
            patch("medasist.generation.chain.build_retriever") as mock_rb,
            patch("medasist.generation.chain.ChatOpenAI") as mock_llm_cls,
        ):
            mock_retriever = MagicMock()
            mock_retriever.invoke.return_value = [_make_doc("texto [1]")]
            mock_rb.return_value = mock_retriever
            mock_llm_cls.return_value = RunnableLambda(boom)

            gen = stream_answer("qual a dose?", stores, UserProfile.MEDICO, settings)

            with pytest.raises(RuntimeError):
                _consume(gen)


class TestBuildStreamChain:
    def test_returns_callable_delegating_to_stream_answer(self) -> None:
        settings = _make_settings()
        stores = MagicMock()

        with (
            patch("medasist.generation.chain.build_retriever") as mock_rb,
            patch("medasist.generation.chain.ChatOpenAI") as mock_llm_cls,
        ):
            mock_retriever = MagicMock()
            mock_retriever.invoke.return_value = [_make_doc("texto [1]")]
            mock_rb.return_value = mock_retriever
            mock_llm_cls.return_value = _make_stream_llm("Resposta [1].")

            stream = build_stream_chain(stores, UserProfile.MEDICO, settings)
            gen = stream("qual a dose?")
            deltas, (citations, is_cold_start) = _consume(gen)

        assert "".join(deltas) == "Resposta [1]."
        assert is_cold_start is False
        assert len(citations) == 1

    def test_closure_passes_doc_types(self) -> None:
        settings = _make_settings()
        stores = MagicMock()

        with (
            patch("medasist.generation.chain.select_collections") as mock_sel,
            patch("medasist.generation.chain.build_retriever") as mock_rb,
        ):
            mock_retriever = MagicMock()
            mock_retriever.invoke.return_value = []
            mock_rb.return_value = mock_retriever
            mock_sel.return_value = {}

            stream = build_stream_chain(stores, UserProfile.MEDICO, settings)
            gen = stream("qual a dose?", doc_types=[DocType.BULA])
            _consume(gen)

        mock_sel.assert_called_once_with(stores, [DocType.BULA])
