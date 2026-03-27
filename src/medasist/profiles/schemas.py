from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

from medasist.config import Settings, get_settings

logger = logging.getLogger(__name__)


class UserProfile(str, Enum):
    """Papel do usuário no sistema MedAssist.

    O valor string corresponde ao prefixo usado em ``Settings``
    (ex: ``medico_temperature``, ``medico_max_tokens``).
    """

    MEDICO = "medico"
    ENFERMEIRO = "enfermeiro"
    ASSISTENTE = "assistente"
    PACIENTE = "paciente"


PROMPT_TEMPLATES: dict[UserProfile, str] = {
    UserProfile.MEDICO: (
        "Você é um assistente médico especializado. Responda de forma técnica, "
        "incluindo mecanismo de ação, posologia e contraindicações "
        "quando relevante.\n\n"
        "Contexto:\n{context}\n\n"
        "Pergunta: {question}\n\n"
        "Resposta:"
    ),
    UserProfile.ENFERMEIRO: (
        "Você é um assistente de enfermagem. Responda de forma clínico-prática, "
        "com foco em administração, cuidados e observações de enfermagem.\n\n"
        "Contexto:\n{context}\n\n"
        "Pergunta: {question}\n\n"
        "Resposta:"
    ),
    UserProfile.ASSISTENTE: (
        "Você é um assistente de saúde. Responda de forma objetiva, "
        "com foco em triagem inicial e encaminhamento adequado.\n\n"
        "Contexto:\n{context}\n\n"
        "Pergunta: {question}\n\n"
        "Resposta:"
    ),
    UserProfile.PACIENTE: (
        "Você é um assistente de saúde. Responda em linguagem simples e acessível, "
        "sem jargão médico, de forma clara e tranquilizadora.\n\n"
        "Contexto:\n{context}\n\n"
        "Pergunta: {question}\n\n"
        "Resposta:"
    ),
}


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
        Se ``Settings`` não possuir os atributos esperados para o perfil,
        ou se o perfil não tiver template configurado.
    """
    if settings is None:
        settings = get_settings()

    key = profile.value
    attr_temp = f"{key}_temperature"
    attr_tokens = f"{key}_max_tokens"

    if not hasattr(settings, attr_temp) or not hasattr(settings, attr_tokens):
        raise ValueError(
            f"Settings não possui atributos para o perfil '{key}'. "
            f"Esperados: '{attr_temp}', '{attr_tokens}'."
        )

    temperature = getattr(settings, attr_temp)
    max_tokens = getattr(settings, attr_tokens)
    prompt_template = PROMPT_TEMPLATES.get(profile)

    if prompt_template is None:
        raise ValueError(f"Perfil sem template configurado: {profile!r}")

    logger.debug(
        "ProfileConfig carregado: profile=%s temperature=%s", key, temperature
    )

    return ProfileConfig(
        temperature=temperature,
        max_tokens=max_tokens,
        prompt_template=prompt_template,
    )
