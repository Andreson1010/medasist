from __future__ import annotations

import logging
import re

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import PromptTemplate

from medasist.config import Settings

logger = logging.getLogger(__name__)

# Mesmo padrão de tokens de palavras do retriever — definido localmente para
# evitar import circular (retriever importa ``rewrite_query`` deste módulo).
_TOKEN_RE = re.compile(r"\b[a-zà-ú0-9]+\b")

# Classe ``ChatOpenAI`` da reescrita, preenchida lazy na primeira chamada a
# ``_expand`` (o import de ``langchain_openai`` ocorre dentro da função —
# mesmo padrão do reranker). Mantém o import deste módulo leve e permite que
# os testes patcheiem ``medasist.retrieval.query_rewrite.ChatOpenAI``.
ChatOpenAI = None  # type: ignore[assignment]

_EXPANSION_PROMPT = PromptTemplate.from_template(
    "Reescreva a consulta médica curta abaixo como uma pergunta completa e "
    "detalhada em português, mantendo o termo do medicamento. Responda APENAS "
    "com a pergunta reescrita, sem preâmbulo, sem comentário, sem aspas e sem "
    "explicação.\n\nConsulta: {query}"
)


def _is_short(query: str, settings: Settings) -> bool:
    """Decide se a consulta é curta (elegível para expansão).

    Tokeniza com ``_TOKEN_RE``, remove as ``retrieval_stopwords`` e considera a
    consulta curta quando o número de tokens de conteúdo é ESTRITAMENTE menor
    que ``retrieval_query_rewrite_min_length`` (default 3). Exatamente o
    mínimo NÃO é curta (limite estrito ``<``).

    Parameters
    ----------
    query : str
        Consulta do usuário.
    settings : Settings
        Configurações com ``retrieval_stopwords`` e
        ``retrieval_query_rewrite_min_length``.

    Returns
    -------
    bool
        ``True`` quando a consulta tem menos tokens de conteúdo que o mínimo.
    """
    tokens = {m.group(0) for m in _TOKEN_RE.finditer(query.lower())}
    stopwords = set(settings.retrieval_stopwords)
    content_tokens = tokens - stopwords
    return len(content_tokens) < settings.retrieval_query_rewrite_min_length


def _expand(query: str, settings: Settings) -> str:
    """Expande uma consulta curta via o LLM local (LM Studio).

    Constrói o ``ChatOpenAI`` lazy (import de ``langchain_openai`` dentro da
    função) com o modelo de reescrita resolvido, temperatura baixa e os limites
    de geração das settings, e invoca a chain de expansão
    ``prompt | llm | StrOutputParser`` com ``{"query": query}``.

    Parameters
    ----------
    query : str
        Consulta curta do usuário.
    settings : Settings
        Configurações de reescrita e do LM Studio.

    Returns
    -------
    str
        Consulta reescrita gerada pelo LLM (sem validação/truncamento).

    Raises
    ------
    Exception
        Qualquer falha na chamada ao LLM (tratada por ``rewrite_query``).
    """
    global ChatOpenAI
    if ChatOpenAI is None:
        from langchain_openai import ChatOpenAI

    llm = ChatOpenAI(
        base_url=settings.lm_studio_base_url,
        api_key=settings.lm_studio_api_key.get_secret_value(),
        model=settings.retrieval_query_rewrite_model,
        temperature=settings.retrieval_query_rewrite_temperature,
        max_tokens=settings.retrieval_query_rewrite_max_tokens,
        max_retries=settings.llm_max_retries,
        request_timeout=settings.llm_request_timeout,
    )
    chain = _EXPANSION_PROMPT | llm | StrOutputParser()
    return chain.invoke({"query": query})


def rewrite_query(query: str, settings: Settings) -> str:
    """Ponto de entrada público da reescrita de consultas curtas.

    Retorna a consulta inalterada quando a flag está desabilitada, quando a
    consulta não é curta, ou quando a chamada ao LLM falha/retorna saída
    inválida. A saída válida é stripada e truncada a
    ``retrieval_query_rewrite_max_output`` caracteres. Nunca propaga exceção.

    Parameters
    ----------
    query : str
        Consulta do usuário.
    settings : Settings
        Configurações de reescrita.

    Returns
    -------
    str
        Consulta reescrita, ou a original em identidade/degradação.
    """
    if not settings.retrieval_query_rewrite_enabled:
        return query
    if not _is_short(query, settings):
        return query

    try:
        rewritten = _expand(query, settings)
    except Exception:
        logger.exception(
            "Query rewrite falhou para '%s' — usando consulta original.",
            query[:50],
        )
        return query

    rewritten = rewritten.strip()
    if not rewritten or not re.search(r"\w", rewritten):
        return query
    if len(rewritten) > settings.retrieval_query_rewrite_max_output:
        rewritten = rewritten[: settings.retrieval_query_rewrite_max_output]
    return rewritten
