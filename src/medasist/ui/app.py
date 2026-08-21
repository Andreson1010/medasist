from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass

import streamlit as st

from medasist.config import Settings, get_settings
from medasist.logging_setup import configure_logging
from medasist.ui.client import (
    APIError,
    CitationResult,
    NotFoundError,
    QueryResult,
    RateLimitError,
    RequestTimeoutError,
    ServerError,
    StreamEvent,
    get_health,
    query,
    query_stream,
)

logger = logging.getLogger(__name__)

_MAX_QUESTION_LEN = 500

# ---------------------------------------------------------------------------
# Constantes de UI
# ---------------------------------------------------------------------------

PROFILE_LABELS: dict[str, str] = {
    "medico": "Médico",
    "enfermeiro": "Enfermeiro",
    "assistente": "Assistente",
    "paciente": "Paciente",
}

DOC_TYPE_LABELS: dict[str, str] = {
    "bula": "Bula",
    "diretriz": "Diretriz",
    "protocolo": "Protocolo",
    "manual": "Manual",
}

_KEY_MESSAGES = "messages"
_KEY_HEALTH_CHECKED = "_health_checked"


# ---------------------------------------------------------------------------
# Helpers de renderização
# ---------------------------------------------------------------------------


def _configure_page() -> None:
    """Configura título, ícone e layout da página Streamlit."""
    st.set_page_config(
        page_title="MedAssist",
        page_icon="🏥",
        layout="centered",
        initial_sidebar_state="expanded",
    )


def _render_sidebar(settings: Settings) -> tuple[str, list[str]]:
    """Renderiza controles na barra lateral e retorna perfil e tipos selecionados.

    Parameters
    ----------
    settings : Settings
        Configurações carregadas do ambiente.

    Returns
    -------
    tuple[str, list[str]]
        ``(profile_key, doc_type_keys)`` selecionados pelo usuário.
    """
    with st.sidebar:
        st.header("Configurações")

        profile_key = st.selectbox(
            "Perfil",
            options=list(PROFILE_LABELS.keys()),
            format_func=lambda k: PROFILE_LABELS[k],
            index=0,
        )

        doc_type_keys = st.multiselect(
            "Filtrar por tipo de documento",
            options=list(DOC_TYPE_LABELS.keys()),
            format_func=lambda k: DOC_TYPE_LABELS[k],
            default=[],
            help="Deixe vazio para consultar todos os tipos.",
        )

        st.divider()
        st.caption(f"⚠️ {settings.disclaimer}")

    return profile_key, doc_type_keys


def _degraded_dependencies(health: dict) -> list[str]:
    """Descreve as dependências não-ok do corpo de /health.

    Parameters
    ----------
    health : dict
        Corpo de ``GET /health``.

    Returns
    -------
    list[str]
        Descrições das dependências cujo status difere de ``ok``, no formato
        ``"Nome (status)"``.
    """
    labels = {"chromadb": "ChromaDB", "lm_studio": "LM Studio"}
    return [
        f"{labels[name]} ({health.get(name, {}).get('status', 'desconhecido')})"
        for name in labels
        if health.get(name, {}).get("status") != "ok"
    ]


def _check_and_warn_health(base_url: str) -> None:
    """Verifica a disponibilidade da API uma vez por sessão.

    O flag de verificação é marcado apenas após a chamada completar,
    garantindo que falhas transientes não suprimam avisos futuros.
    Exibe ``st.warning`` quando a API está fora do ar (HTTP não-200) ou quando
    está no ar mas com dependências degradadas.

    Parameters
    ----------
    base_url : str
        URL base da API MedAssist.
    """
    if st.session_state.get(_KEY_HEALTH_CHECKED):
        return

    health = get_health(base_url)
    st.session_state[_KEY_HEALTH_CHECKED] = True

    if health is None:
        st.warning(
            "A API MedAssist está indisponível. "
            "Verifique se o servidor está em execução em: " + base_url,
            icon="⚠️",
        )
    elif health.get("status") == "degraded":
        degraded = ", ".join(_degraded_dependencies(health))
        st.warning(
            "A API está no ar, mas há dependências degradadas: " + degraded,
            icon="⚠️",
        )


def _render_chat_history(settings: Settings) -> None:
    """Reproduz o histórico de mensagens com fidelidade total.

    Mensagens de assistente são re-renderizadas via ``_render_response``
    para garantir que disclaimer e citações estejam sempre presentes.

    Parameters
    ----------
    settings : Settings
        Configurações com mensagens de segurança médica.
    """
    for message in st.session_state[_KEY_MESSAGES]:
        with st.chat_message(message["role"]):
            if message["role"] == "assistant" and message.get("result") is not None:
                _render_response(message["result"], settings)
            else:
                st.markdown(message["content"])


def _format_citation(c: CitationResult) -> str:
    """Formata uma citação no padrão exigido pelas regras médicas.

    Parameters
    ----------
    c : CitationResult
        Dados da citação retornada pela API.

    Returns
    -------
    str
        String no formato ``[N] source — Seção: section, Pág. page``.
    """
    parts = [f"[{c.index}] {c.source}"]
    if c.section:
        parts.append(f"Seção: {c.section}")
    if c.page:
        parts.append(f"Pág. {c.page}")
    return " — ".join(parts)


def _render_response(result: QueryResult, settings: Settings) -> None:
    """Renderiza a resposta do pipeline RAG no chat.

    Para cold start, exibe mensagem fixa e disclaimer — nunca o ``result.answer``
    como conteúdo principal. Para respostas normais, exibe resposta, citações e
    disclaimer. Ambos os caminhos garantem a presença obrigatória do disclaimer.

    Parameters
    ----------
    result : QueryResult
        Resultado tipado retornado pelo client.
    settings : Settings
        Configurações com mensagens de segurança médica.
    """
    if result.is_cold_start:
        st.warning(settings.cold_start_message, icon="🔍")
        st.info(result.disclaimer, icon="ℹ️")
        return

    st.markdown(result.answer)

    if result.citations:
        with st.expander("Fontes consultadas", expanded=False):
            for citation in result.citations:
                st.caption(_format_citation(citation))

    st.caption(f"ℹ️ {result.disclaimer}")


def _handle_error(exc: APIError) -> None:
    """Mapeia subclasses de ``APIError`` para mensagens de UI adequadas.

    Detalhes internos são registrados apenas no log, nunca exibidos ao usuário.

    Parameters
    ----------
    exc : APIError
        Exceção levantada pelo client HTTP.
    """
    if isinstance(exc, RateLimitError):
        st.warning(
            "Muitas requisições em pouco tempo. Aguarde um momento e tente novamente.",
            icon="⏱️",
        )
    elif isinstance(exc, RequestTimeoutError):
        st.error(
            "A API não respondeu a tempo. Verifique sua conexão e tente novamente.",
            icon="⏰",
        )
    elif isinstance(exc, ServerError):
        st.error(
            "Erro interno no servidor. Tente novamente em alguns instantes.",
            icon="🔴",
        )
    else:
        st.error("Erro na comunicação com a API. Tente novamente.", icon="❌")

    logger.warning("Erro na consulta: %s — %s", type(exc).__name__, exc)


# ---------------------------------------------------------------------------
# Streaming (SSE) helpers
# ---------------------------------------------------------------------------


@dataclass
class _StreamState:
    """Estado acumulado durante o consumo de um stream SSE.

    Attributes
    ----------
    answer : str
        Resposta completa acumulada a partir dos deltas.
    citations : list[CitationResult] | None
        Citações recebidas no evento ``citations``.
    disclaimer : str | None
        Texto do disclaimer recebido.
    is_cold_start : bool
        True quando o evento ``cold_start`` foi recebido.
    error : str | None
        Mensagem de erro recebida (evento ``error`` terminal).
    done : bool
        True quando o evento ``done`` foi recebido.
    """

    answer: str = ""
    citations: list[CitationResult] | None = None
    disclaimer: str | None = None
    is_cold_start: bool = False
    error: str | None = None
    done: bool = False


def _delta_generator(
    events: Iterator[StreamEvent], state: _StreamState
) -> Iterator[str]:
    """Consome os eventos do stream, acumulando o estado e yieldando deltas.

    Os eventos ``token`` são repassados a ``st.write_stream`` (yield do delta)
    e acumulados em ``state.answer``. Os demais eventos (terminais) apenas
    atualizam o estado por closure, sem serem renderizados.

    Parameters
    ----------
    events : Iterator[StreamEvent]
        Iterador de eventos tipados do client.
    state : _StreamState
        Estado acumulado, mutado ao longo do consumo.

    Yields
    ------
    str
        Cada delta de um evento ``token``, para renderização incremental.
    """
    for event in events:
        if event.type == "token":
            state.answer += event.delta or ""
            yield event.delta or ""
        elif event.type == "citations":
            state.citations = event.citations
        elif event.type == "disclaimer":
            state.disclaimer = event.text
        elif event.type == "cold_start":
            state.is_cold_start = True
        elif event.type == "error":
            state.error = event.message
        elif event.type == "done":
            state.done = True


def _build_stream_result(
    state: _StreamState, profile: str, settings: Settings
) -> QueryResult | None:
    """Reconstrói um ``QueryResult`` a partir do estado terminal do stream.

    Só persiste resposta em sucesso (evento ``done`` sem erro e sem cold
    start). Em ``error``, ``cold_start`` ou stream incompleto, retorna ``None``
    para que a UI descarte o parcial.

    Parameters
    ----------
    state : _StreamState
        Estado acumulado durante o stream.
    profile : str
        Perfil de usuário utilizado na consulta.
    settings : Settings
        Configurações com o disclaimer médico.

    Returns
    -------
    QueryResult | None
        Resultado persistível em sucesso, ou ``None`` caso contrário.
    """
    if state.error is not None or state.is_cold_start or not state.done:
        return None
    return QueryResult(
        answer=state.answer,
        citations=state.citations or [],
        profile=profile,
        disclaimer=state.disclaimer or settings.disclaimer,
        is_cold_start=False,
    )


def _persist_assistant(result: QueryResult) -> None:
    """Adiciona uma resposta de assistente ao histórico da sessão.

    Parameters
    ----------
    result : QueryResult
        Resultado a persistir no histórico.
    """
    st.session_state[_KEY_MESSAGES].append(
        {
            "role": "assistant",
            "content": result.answer,
            "result": result,
        }
    )


def _render_streaming(
    question: str,
    profile: str,
    doc_types: list[str] | None,
    settings: Settings,
) -> None:
    """Renderiza a resposta incrementalmente via ``st.write_stream``.

    Consome ``query_stream`` num gerador que acumula a resposta e captura os
    eventos terminais por closure. Ao concluir, decide pelo estado terminal:
    sucesso → renderiza citações + disclaimer e persiste o ``QueryResult``;
    ``cold_start`` → descarta o texto e mostra a mensagem fixa + disclaimer;
    ``error`` → não persiste o parcial. Se o backend responder 404 (flag
    divergente), degrada para o caminho não-streaming ``/query``.

    Parameters
    ----------
    question : str
        Pergunta do usuário.
    profile : str
        Perfil de usuário.
    doc_types : list[str] | None
        Filtro opcional por tipo de documento.
    settings : Settings
        Configurações com mensagens de segurança e timeout.
    """
    state = _StreamState()

    try:
        events = query_stream(
            question=question,
            profile=profile,
            doc_types=doc_types,
            base_url=settings.api_base_url,
            timeout=settings.ui_request_timeout,
        )
        st.write_stream(_delta_generator(events, state))
    except NotFoundError:
        # Backend com streaming desabilitado: degrada para /query.
        result = query(
            question=question,
            profile=profile,
            doc_types=doc_types,
            base_url=settings.api_base_url,
            timeout=settings.ui_request_timeout,
        )
        _render_response(result, settings)
        _persist_assistant(result)
        return
    except APIError as exc:
        _handle_error(exc)
        return

    result = _build_stream_result(state, profile, settings)
    if result is None:
        if state.error is not None:
            st.error(
                "Erro ao gerar a resposta. Tente novamente em alguns instantes.",
                icon="🔴",
            )
        elif state.is_cold_start:
            st.warning(settings.cold_start_message, icon="🔍")
            st.info(state.disclaimer or settings.disclaimer, icon="ℹ️")
        return

    _render_response(result, settings)
    _persist_assistant(result)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Ponto de entrada da aplicação MedAssist no Streamlit.

    Gerencia configuração de página, sidebar, verificação de saúde da API,
    histórico de chat e o loop principal de consulta/resposta. Configura o
    logging estruturado no início, antes de qualquer log relevante.
    """
    _configure_page()
    settings = get_settings()
    configure_logging(settings, "ui")

    if _KEY_MESSAGES not in st.session_state:
        st.session_state[_KEY_MESSAGES] = []

    profile_key, doc_type_keys = _render_sidebar(settings)
    _check_and_warn_health(settings.api_base_url)

    st.title("MedAssist")
    st.caption("Assistente de informações médicas baseado em documentos clínicos.")

    _render_chat_history(settings)

    if prompt := st.chat_input("Digite sua pergunta médica..."):
        if len(prompt) > _MAX_QUESTION_LEN:
            st.warning(
                f"Pergunta muito longa. O limite é {_MAX_QUESTION_LEN} caracteres "
                f"(atual: {len(prompt)}).",
                icon="✂️",
            )
        else:
            st.session_state[_KEY_MESSAGES].append(
                {"role": "user", "content": prompt, "result": None}
            )

            with st.chat_message("user"):
                st.markdown(prompt)

            with st.chat_message("assistant"):
                if settings.generation_streaming_enabled:
                    _render_streaming(
                        question=prompt,
                        profile=profile_key,
                        doc_types=doc_type_keys or None,
                        settings=settings,
                    )
                else:
                    with st.spinner("Consultando..."):
                        try:
                            result = query(
                                question=prompt,
                                profile=profile_key,
                                doc_types=doc_type_keys or None,
                                base_url=settings.api_base_url,
                                timeout=settings.ui_request_timeout,
                            )
                            _render_response(result, settings)
                            _persist_assistant(result)

                        except APIError as exc:
                            _handle_error(exc)


if __name__ == "__main__":
    main()
