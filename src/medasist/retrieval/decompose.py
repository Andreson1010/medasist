from __future__ import annotations

import logging
import re

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import PromptTemplate

from medasist.config import Settings

logger = logging.getLogger(__name__)

# Mesmo padrão de tokens de palavras do retriever — definido localmente para
# evitar import circular (chain importa ``decompose_query`` deste módulo).
_TOKEN_RE = re.compile(r"\b[a-zà-ú0-9]+\b")

# Conectores de coordenação reconhecidos na heurística ``_is_compound`` (Q4).
# ``e`` é stopword — por isso a detecção ocorre no texto bruto (pré-remoção de
# stopwords), sobre o conjunto de TODOS os tokens da query, não só os de
# conteúdo.
_CONNECTORS = frozenset({"e", "ou", "e/ou"})

# Classe ``ChatOpenAI`` do split, preenchida lazy na primeira chamada a
# ``_split`` (o import de ``langchain_openai`` ocorre dentro da função — mesmo
# padrão de ``query_rewrite.py``). Mantém o import deste módulo leve e permite
# que os testes patcheiem ``medasist.retrieval.decompose.ChatOpenAI``.
ChatOpenAI = None  # type: ignore[assignment]

_DECOMPOSE_PROMPT = PromptTemplate.from_template(
    "Divida a pergunta médica composta abaixo em perguntas menores e "
    "independentes, cada uma com uma única intenção de busca. Escreva UMA "
    "sub-pergunta por linha, sem numeração, sem preâmbulo, sem comentário, sem "
    "aspas e sem explicação. Não repita a pergunta original: apenas as "
    "sub-perguntas.\n\nPergunta: {query}"
)


def _is_compound(query: str, settings: Settings) -> bool:
    """Decide deterministicamente se a pergunta é composta (heurística Q4).

    Considera composta quando o conjunto de TODOS os tokens da query (via
    ``_TOKEN_RE``, SEM remoção de stopwords) tem pelo menos
    ``retrieval_decompose_min_tokens`` itens E (a) contém um conector de
    coordenação (``e``, ``ou``, ``e/ou``) detectado no texto bruto — pré-
    remoção de stopwords, pois ``e`` é stopword mas é conector — OU (b) uma
    vírgula seguida de mais tokens de conteúdo (``_has_comma_with_content``).

    Parameters
    ----------
    query : str
        Pergunta do usuário.
    settings : Settings
        Configurações com ``retrieval_stopwords`` e
        ``retrieval_decompose_min_tokens``.

    Returns
    -------
    bool
        ``True`` quando a pergunta é considerada composta.
    """
    tokens = {m.group(0) for m in _TOKEN_RE.finditer(query.lower())}

    if len(tokens) < settings.retrieval_decompose_min_tokens:
        return False
    if _CONNECTORS & tokens:
        return True
    stopwords = set(settings.retrieval_stopwords)
    return _has_comma_with_content(query, stopwords)


def _has_comma_with_content(query: str, stopwords: set[str]) -> bool:
    """Verifica se há vírgula seguida de pelo menos um token de conteúdo.

    Parameters
    ----------
    query : str
        Pergunta do usuário.
    stopwords : set[str]
        Stopwords de ``retrieval_stopwords``.

    Returns
    -------
    bool
        ``True`` quando uma vírgula é seguida de mais tokens de conteúdo.
    """
    parts = query.lower().split(",")
    for part in parts[1:]:
        tokens = {m.group(0) for m in _TOKEN_RE.finditer(part)}
        if tokens - stopwords:
            return True
    return False


def _split(query: str, settings: Settings) -> list[str]:
    """Divide a pergunta composta em sub-perguntas via o LLM local (LM Studio).

    Constrói o ``ChatOpenAI`` lazy (import de ``langchain_openai`` dentro da
    função) com o modelo de decomposição resolvido, temperatura baixa e os
    limites de geração das settings, e invoca a chain
    ``_DECOMPOSE_PROMPT | llm | StrOutputParser``. A saída é parseada linha a
    linha (strip e filtro de vazias) e truncada a
    ``retrieval_decompose_max_sub_questions`` itens.

    Parameters
    ----------
    query : str
        Pergunta composta do usuário.
    settings : Settings
        Configurações de decomposição e do LM Studio.

    Returns
    -------
    list[str]
        Sub-perguntas extraídas (no máximo ``max_sub_questions``).

    Raises
    ------
    Exception
        Qualquer falha na chamada ao LLM (tratada por ``decompose_query``).
    """
    global ChatOpenAI
    if ChatOpenAI is None:
        from langchain_openai import ChatOpenAI

    llm = ChatOpenAI(
        base_url=settings.lm_studio_base_url,
        api_key=settings.lm_studio_api_key.get_secret_value(),
        model=settings.retrieval_decompose_model,
        temperature=settings.retrieval_decompose_temperature,
        max_tokens=settings.retrieval_decompose_max_tokens,
        max_retries=settings.llm_max_retries,
        request_timeout=settings.llm_request_timeout,
    )
    chain = _DECOMPOSE_PROMPT | llm | StrOutputParser()
    raw = chain.invoke({"query": query})
    subs = [line.strip() for line in raw.splitlines() if line.strip()]
    return subs[: settings.retrieval_decompose_max_sub_questions]


def decompose_query(query: str, settings: Settings) -> list[str]:
    """Ponto de entrada público da decomposição de perguntas compostas.

    Retorna ``[query]`` (identidade) quando a flag está desabilitada, quando a
    pergunta não é composta (gate Q4, sem chamar o LLM), quando o LLM de split
    falha/timeout, ou quando retorna 0/1 sub-pergunta válida. Em falha, loga
    com ``logger.exception`` sem propagar. Nunca propaga exceção.

    Parameters
    ----------
    query : str
        Pergunta do usuário.
    settings : Settings
        Configurações de decomposição.

    Returns
    -------
    list[str]
        Sub-perguntas, ou ``[query]`` em identidade/degradação.
    """
    if not settings.retrieval_decompose_enabled:
        return [query]
    if not _is_compound(query, settings):
        return [query]

    try:
        subs = _split(query, settings)
    except Exception:
        logger.exception(
            "Decomposição falhou para '%s' — usando pergunta original.",
            query[:50],
        )
        return [query]

    if len(subs) <= 1:
        return [query]
    return subs
