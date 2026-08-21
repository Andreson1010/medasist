from __future__ import annotations

import logging
import os

import pytest

from medasist.config import Settings
from medasist.logging_setup import _HANDLER_NAME_PREFIX, _configured_apps

_ADMIN_KEY = "test-admin-key-0123456789"

os.environ.setdefault("ADMIN_API_KEY", _ADMIN_KEY)


@pytest.fixture(autouse=True)
def _cleanup_logging_state() -> None:
    """Restaura o estado global de logging após cada teste.

    ``configure_logging`` instala handlers no logger raiz e altera o nível
    raiz; o fixture remove os handlers do MedAssist e restaura o nível para
    não contaminar testes seguintes.
    """
    root = logging.getLogger()
    original_level = root.level
    yield
    for handler in list(root.handlers):
        if handler.get_name() and handler.get_name().startswith(_HANDLER_NAME_PREFIX):
            handler.close()
            root.removeHandler(handler)
    _configured_apps.clear()
    root.setLevel(original_level)


@pytest.fixture(autouse=True)
def _reset_rate_limiter() -> None:
    """Zera o storage do slowapi antes de cada teste.

    O limiter usa storage em memória compartilhado entre testes. Com o rate
    limit efetivamente ativo (CRIT-01), testes que fazem várias requisições a
    /query e /query/stream ultrapassariam o limite de 10/min acumulado entre
    testes. Zerar o storage a cada teste isola o contador por teste.
    """
    from medasist.api.deps import limiter

    limiter.reset()
    yield


@pytest.fixture()
def settings() -> Settings:
    """Settings com valores de teste (sem .env real)."""
    return Settings(
        lm_studio_api_key="lm-studio-test",
        admin_api_key=_ADMIN_KEY,
    )
