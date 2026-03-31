from __future__ import annotations

import logging

import streamlit as st

from medasist.config import Settings, get_settings
from medasist.ui.client import (
    APIError,
    CitationResult,
    QueryResult,
    RateLimitError,
    RequestTimeoutError,
    ServerError,
    check_health,
    query,
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


def _check_and_warn_health(base_url: str) -> None:
    """Verifica a disponibilidade da API uma vez por sessão.

    O flag de verificação é marcado apenas após a chamada completar,
    garantindo que falhas transientes não suprimam avisos futuros.
    Exibe ``st.warning`` se a API estiver indisponível.

    Parameters
    ----------
    base_url : str
        URL base da API MedAssist.
    """
    if st.session_state.get(_KEY_HEALTH_CHECKED):
        return

    api_ok = check_health(base_url)
    st.session_state[_KEY_HEALTH_CHECKED] = True

    if not api_ok:
        st.warning(
            "A API MedAssist está indisponível. "
            "Verifique se o servidor está em execução em: " + base_url,
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
    return f"[{c.index}] {c.source} — Seção: {c.section}, Pág. {c.page}"


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
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Ponto de entrada da aplicação MedAssist no Streamlit.

    Gerencia configuração de página, sidebar, verificação de saúde da API,
    histórico de chat e o loop principal de consulta/resposta.
    """
    _configure_page()
    settings = get_settings()

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
                with st.spinner("Consultando..."):
                    try:
                        result = query(
                            question=prompt,
                            profile=profile_key,
                            doc_types=doc_type_keys or None,
                            base_url=settings.api_base_url,
                        )
                        _render_response(result, settings)
                        st.session_state[_KEY_MESSAGES].append(
                            {"role": "assistant", "content": result.answer, "result": result}
                        )

                    except APIError as exc:
                        _handle_error(exc)


if __name__ == "__main__":
    main()
