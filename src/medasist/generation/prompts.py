from __future__ import annotations

import logging
import threading

from langchain_core.prompts import ChatPromptTemplate

from medasist.profiles.schemas import PROMPT_TEMPLATES, UserProfile

logger = logging.getLogger(__name__)


class PromptRegistry:
    """Registro lazy e thread-safe de ChatPromptTemplate por UserProfile.

    Converte os templates string de ``PROMPT_TEMPLATES`` em instâncias de
    ``ChatPromptTemplate`` sob demanda. Cada template é construído uma única
    vez e reutilizado nas chamadas seguintes (cache por instância).

    Thread-safe: usa ``threading.Lock`` para proteger a construção do cache
    contra condições de corrida em ambientes com múltiplas threads (FastAPI).

    Examples
    --------
    >>> registry = PromptRegistry()
    >>> prompt = registry.get_prompt(UserProfile.MEDICO)
    >>> messages = prompt.format_messages(context="...", question="Qual a dose?")
    """

    def __init__(self) -> None:
        self._cache: dict[UserProfile, ChatPromptTemplate] = {}
        self._lock = threading.Lock()

    def get_prompt(self, profile: UserProfile) -> ChatPromptTemplate:
        """Retorna o ChatPromptTemplate para o perfil informado.

        Parameters
        ----------
        profile : UserProfile
            Perfil do usuário.

        Returns
        -------
        ChatPromptTemplate
            Template com variáveis ``{context}`` e ``{question}``.

        Raises
        ------
        KeyError
            Se o perfil não possuir template em ``PROMPT_TEMPLATES``.
        """
        if profile not in self._cache:
            with self._lock:
                if profile not in self._cache:
                    template_str = PROMPT_TEMPLATES[profile]
                    self._cache[profile] = ChatPromptTemplate.from_template(
                        template_str
                    )
                    logger.debug(
                        "PromptRegistry: template construído para perfil '%s'.",
                        profile.value,
                    )
        return self._cache[profile]
