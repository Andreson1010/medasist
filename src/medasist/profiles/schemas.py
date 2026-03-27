from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

from medasist.config import Settings, get_settings

logger = logging.getLogger(__name__)

_PROMPT_TEMPLATES: dict[str, str] = {
    "medico": (
        "Você é um assistente médico especializado. Responda de forma técnica, "
        "incluindo mecanismo de ação, posologia e contraindicações "
        "quando relevante.\n\n"
        "Contexto:\n{context}\n\n"
        "Pergunta: {question}\n\n"
        "Resposta:"
    ),
    "enfermeiro": (
        "Você é um assistente de enfermagem. Responda de forma clínico-prática, "
        "com foco em administração, cuidados e observações de enfermagem.\n\n"
        "Contexto:\n{context}\n\n"
        "Pergunta: {question}\n\n"
        "Resposta:"
    ),
    "assistente": (
        "Você é um assistente de saúde. Responda de forma objetiva, "
        "com foco em triagem inicial e encaminhamento adequado.\n\n"
        "Contexto:\n{context}\n\n"
        "Pergunta: {question}\n\n"
        "Resposta:"
    ),
    "paciente": (
        "Você é um assistente de saúde. Responda em linguagem simples e acessível, "
        "sem jargão médico, de forma clara e tranquilizadora.\n\n"
        "Contexto:\n{context}\n\n"
        "Pergunta: {question}\n\n"
        "Resposta:"
    ),
}


class UserProfile(str, Enum):
    """Papel do usuário no sistema MedAssist.

    O valor string corresponde ao prefixo usado em ``Settings``
    (ex: ``medico_temperature``, ``medico_max_tokens``).
    """

    MEDICO = "medico"
    ENFERMEIRO = "enfermeiro"
    ASSISTENTE = "assistente"
    PACIENTE = "paciente"


@dataclass(frozen=True)
class ProfileConfig:
    """Configuração imutável de LLM para um perfil de usuário.

    Attributes
    ----------
    temperature : float
        Temperatura de amostragem do LLM.
    max_tokens : int
        Número máximo de tokens na resposta gerada.
    prompt_template : str
        Template de prompt com placeholders ``{context}`` e ``{question}``.
    """

    temperature: float
    max_tokens: int
    prompt_template: str


def get_profile_config(
    profile: UserProfile,
    settings: Settings | None = None,
) -> ProfileConfig:
    """Retorna a configuração de LLM para o perfil informado.

    Parameters
    ----------
    profile : UserProfile
        Perfil do usuário.
    settings : Settings | None
        Instância de configurações. Se ``None``, usa o singleton ``get_settings()``.

    Returns
    -------
    ProfileConfig
        Configuração imutável com temperatura, max_tokens e prompt_template.

    Raises
    ------
    ValueError
        Se o perfil não tiver configuração mapeada (nunca deve ocorrer).
    """
    if settings is None:
        settings = get_settings()

    key = profile.value
    temperature = getattr(settings, f"{key}_temperature")
    max_tokens = getattr(settings, f"{key}_max_tokens")
    prompt_template = _PROMPT_TEMPLATES.get(key)

    if prompt_template is None:
        raise ValueError(f"Perfil sem template configurado: {profile!r}")

    logger.debug("ProfileConfig carregado: profile=%s temperature=%s", key, temperature)

    return ProfileConfig(
        temperature=temperature,
        max_tokens=max_tokens,
        prompt_template=prompt_template,
    )
