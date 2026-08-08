from __future__ import annotations

import os

import pytest

from medasist.config import Settings

_ADMIN_KEY = "test-admin-key-0123456789"

os.environ.setdefault("ADMIN_API_KEY", _ADMIN_KEY)


@pytest.fixture()
def settings() -> Settings:
    """Settings com valores de teste (sem .env real)."""
    return Settings(
        lm_studio_api_key="lm-studio-test",
        admin_api_key=_ADMIN_KEY,
    )
