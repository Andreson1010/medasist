from __future__ import annotations

import pytest


@pytest.fixture
def base_url() -> str:
    """URL base da API para testes."""
    return "http://test-api"
