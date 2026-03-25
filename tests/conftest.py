from __future__ import annotations

import pytest

from medasist.config import Settings


@pytest.fixture()
def settings() -> Settings:
    """Settings com valores de teste (sem .env real)."""
    return Settings(
        lm_studio_api_key="lm-studio-test",
        admin_api_key="test-admin-key",
    )
