from __future__ import annotations

import logging
import re
import threading
import unicodedata
from typing import Any

from langchain_core.documents import Document

from medasist.config import Settings

logger = logging.getLogger(__name__)

# Tokens esparsos: minúsculas + dígitos, sem acentos (normalizados antes).
# Captura dosagens dígito-unidade como token íntegro (ex: "500mg").
_TOKEN_RE = re.compile(r"\b[a-z0-9]+\b")

# Normaliza o espaço entre dígito e unidade de dosagem ("10 mg" -> "10mg").
_DIGIT_UNIT_RE = re.compile(r"(\d+)\s+(mg|ml|g|kg)\b")

_cache: dict[str, SparseIndex | None] = {}
_cache_lock = threading.Lock()


def _strip_diacritics(text: str) -> str:
    """Remove diacríticos (acentos) de um texto usando decomposição NFKD.

    Parâmetros preservados (ex: ``ç``, ``ã``) são convertidos para a forma
    ASCII equivalente, de modo que ``"Dipironá"`` e ``"dipirona"`` casem.

    Parameters
    ----------
    text : str
        Texto de entrada.

    Returns
    -------
    str
        Texto sem caracteres combináveis de acentuação.
    """
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def _normalized_stopwords(stopwords: tuple[str, ...] | list[str]) -> frozenset[str]:
    """Normaliza uma lista de stopwords para comparação com tokens esparsos.

    Cada stopword é convertida para minúsculas e sem diacríticos, alinhada à
    normalização aplicada aos tokens (acentos em ambos os sentidos casam).

    Parameters
    ----------
    stopwords : tuple[str, ...] | list[str]
        Stopwords esparsas da configuração.

    Returns
    -------
    frozenset[str]
        Conjunto de stopwords normalizadas.
    """
    return frozenset(_strip_diacritics(w.lower()) for w in stopwords)


def _tokenize(text: str, stopwords: frozenset[str]) -> list[str]:
    """Tokeniza texto normalizado (minúsculas, sem acentos, dosagens íntegras).

    Parameters
    ----------
    text : str
        Texto a tokenizar.
    stopwords : frozenset[str]
        Stopwords esparsas já normalizadas.

    Returns
    -------
    list[str]
        Tokens normalizados, sem stopwords. Vazio para consulta só de
        stopwords (cai para dense-only).
    """
    normalized = _strip_diacritics(text.lower())
    normalized = _DIGIT_UNIT_RE.sub(r"\1\2", normalized)
    tokens = [m.group(0) for m in _TOKEN_RE.finditer(normalized)]
    return [t for t in tokens if t not in stopwords]


def tokenize(text: str, settings: Settings) -> list[str]:
    """Tokeniza texto para busca esparsa (BM25), normalizado para PT-BR.

    Aplica minúsculas, remoção de diacríticos (acentos em ambos os sentidos
    casam), preserva dosagens dígito-unidade como token íntegro ("500mg") e
    normaliza o espaço dígito-unidade ("10 mg"/"10mg" -> ``10mg``). Usa
    ``retrieval_sparse_stopwords`` própria — NÃO usa ``retrieval_stopwords``,
    de modo que ``mg/ml/g/kg`` são preservados.

    Parameters
    ----------
    text : str
        Texto (query ou chunk) a tokenizar.
    settings : Settings
        Configurações com ``retrieval_sparse_stopwords``.

    Returns
    -------
    list[str]
        Tokens normalizados, sem stopwords esparsas.
    """
    stopwords = _normalized_stopwords(settings.retrieval_sparse_stopwords)
    return _tokenize(text, stopwords)


def _reconstruct_document(text: str, meta: dict[str, Any]) -> Document:
    """Reconstrói um ``Document`` a partir do corpus armazenado no ChromaDB.

    Mantém fidelidade de metadados com o caminho denso: ``page_content`` e as
    chaves ``doc_type``, ``source_path``, ``sha256``, ``chunk_index``,
    ``char_count``, ``page`` (sentinela 0 quando desconhecida) e ``section``,
    exatamente como gravadas na coleção.

    Parameters
    ----------
    text : str
        Conteúdo do chunk (``documents`` da coleção).
    meta : dict[str, Any]
        Metadados do chunk (``metadatas`` da coleção).

    Returns
    -------
    Document
        Documento reconstruído com metadados fiéis ao armazenado.
    """
    meta = meta or {}
    return Document(
        page_content=text,
        metadata={
            "doc_type": meta.get("doc_type", ""),
            "source_path": meta.get("source_path", ""),
            "sha256": meta.get("sha256", ""),
            "chunk_index": meta.get("chunk_index", ""),
            "char_count": meta.get("char_count", ""),
            "page": meta.get("page", 0),
            "section": meta.get("section", ""),
        },
    )


class SparseIndex:
    """Índice BM25 em memória, por DocType, construído lazy do ChromaDB.

    Mantém o corpus tokenizado e o modelo ``BM25Okapi`` para busca esparsa.
    É reconstruído quando a versão da coleção muda (ver ``is_stale``), de modo
    que chunks ingeridos após a construção ficam visíveis na query seguinte.

    Parameters
    ----------
    name : str
        Nome da coleção ChromaDB de origem.
    docs : list[Document]
        Documentos reconstruídos do corpus.
    tokenized : list[list[str]]
        Corpus tokenizado, alinhado a ``docs``.
    stopwords : frozenset[str]
        Stopwords esparsas normalizadas.
    corpus_count : int
        Contagem de documentos no momento da construção.
    """

    def __init__(
        self,
        name: str,
        docs: list[Document],
        tokenized: list[list[str]],
        stopwords: frozenset[str],
        corpus_count: int,
        collection: Any,
    ) -> None:
        self._name = name
        self._docs = docs
        self._tokenized = tokenized
        self._stopwords = stopwords
        self._corpus_count = corpus_count
        self._collection = collection
        self._bm25: Any = None
        if tokenized:
            from rank_bm25 import BM25Okapi

            self._bm25 = BM25Okapi(tokenized)

    @classmethod
    def build(cls, store: Any, settings: Settings) -> SparseIndex:
        """Constrói um índice esparso a partir de um vectorstore ChromaDB.

        Faz snapshot read-only do corpus via ``store._collection.get``
        (mesmo acesso do probe de avaliação) e tokeniza cada documento.

        Parameters
        ----------
        store : Chroma
            Vectorstore LangChain com ``_collection`` acessível.
        settings : Settings
            Configurações com ``retrieval_sparse_stopwords``.

        Returns
        -------
        SparseIndex
            Índice construído sobre o corpus atual da coleção.

        Raises
        ------
        Exception
            Qualquer falha no acesso à coleção ou na tokenização é propagada
            para ser tratada por ``get_sparse_index`` (degradação graciosa).
        """
        collection = store._collection
        data = collection.get(include=["documents", "metadatas"])
        documents = data.get("documents", [])
        metadatas = data.get("metadatas", [])
        ids = data.get("ids", [])

        docs = [
            _reconstruct_document(text, meta)
            for text, meta in zip(documents, metadatas, strict=False)
        ]
        stopwords = _normalized_stopwords(settings.retrieval_sparse_stopwords)
        tokenized = [_tokenize(d.page_content, stopwords) for d in docs]
        return cls(collection.name, docs, tokenized, stopwords, len(ids), collection)

    def search(self, query: str, top_k: int) -> list[tuple[Document, float]]:
        """Busca os ``top_k`` documentos mais relevantes por score BM25.

        Retorna pares ``(Document, score)`` ordenados por score decrescente.
        Consultas sem tokens (só stopwords) ou corpus vazio resultam em lista
        vazia, sem erro.

        Parameters
        ----------
        query : str
            Consulta do usuário.
        top_k : int
            Número máximo de candidatos a retornar.

        Returns
        -------
        list[tuple[Document, float]]
            Pares ``(Document, score BM25)`` ordenados por score desc.
        """
        query_tokens = set(_tokenize(query, self._stopwords))
        if not query_tokens or self._bm25 is None:
            return []
        scores = self._bm25.get_scores(list(query_tokens))
        matched = [
            i
            for i, tokens in enumerate(self._tokenized)
            if set(tokens) & query_tokens
        ]
        ordered = sorted(matched, key=lambda i: scores[i], reverse=True)[:top_k]
        return [(self._docs[i], float(scores[i])) for i in ordered]

    def is_stale(self) -> bool:
        """Indica se o índice está desatualizado em relação à coleção.

        A checagem compara a contagem atual da coleção com a contagem no
        momento da construção; se mudou, o índice deve ser reconstruído
        (decisão Q2 — refresh lazy pós-ingest). Falha na checagem é tratada
        como "não obsoleto" para não interromper o fluxo.

        Returns
        -------
        bool
            ``True`` quando a coleção mudou desde a construção.
        """
        try:
            return self._collection.count() != self._corpus_count
        except Exception:
            logger.exception("Falha ao checar staleness do índice '%s'.", self._name)
            return False


def _collection_identity(store: Any) -> str:
    """Identidade estável da coleção para o cache do índice esparso.

    Parameters
    ----------
    store : Chroma
        Vectorstore com ``_collection``.

    Returns
    -------
    str
        Nome da coleção ChromaDB (chave do cache).
    """
    return store._collection.name


def _try_build(store: Any, settings: Settings) -> SparseIndex | None:
    """Tenta construir o índice esparso, logando falha e degradando.

    Nunca propaga exceção: em falha loga ``logger.exception`` e retorna
    ``None`` (dense-only), preservando o contexto denso válido.

    Parameters
    ----------
    store : Chroma
        Vectorstore com ``_collection``.
    settings : Settings
        Configurações esparsas.

    Returns
    -------
    SparseIndex | None
        Índice construído ou ``None`` em falha.
    """
    try:
        return SparseIndex.build(store, settings)
    except Exception:
        logger.exception("Falha ao construir índice esparso BM25.")
        return None


def get_sparse_index(store: Any, settings: Settings) -> SparseIndex | None:
    """Retorna o singleton lazy do índice esparso para a coleção.

    Constrói o índice uma única vez por coleção (double-checked locking) e o
    reutiliza nas queries seguintes. Quando o índice existente está obsoleto
    (coleção mudou), reconstrói. Em falha de construção, retorna ``None`` e
    mantém o cache sem entrada válida — nunca propaga.

    Parameters
    ----------
    store : Chroma
        Vectorstore com ``_collection``.
    settings : Settings
        Configurações esparsas.

    Returns
    -------
    SparseIndex | None
        Índice da coleção, ou ``None`` se indisponível.
    """
    key = _collection_identity(store)
    index = _cache.get(key)
    if index is not None and not index.is_stale():
        return index

    with _cache_lock:
        index = _cache.get(key)
        if index is not None and not index.is_stale():
            return index
        new_index = _try_build(store, settings)
        if new_index is not None:
            _cache[key] = new_index
        return new_index


def reset_sparse_indexes() -> None:
    """Limpa o cache global de índices esparsos.

    Hook usado em fixtures de teste para que o singleton lazy não vaze estado
    entre testes (mitigação CONCERNS L4).

    Returns
    -------
    None
    """
    with _cache_lock:
        _cache.clear()
