from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest

from medasist.api.main import lifespan
from medasist.config import ADMIN_KEY_MIN_LENGTH


@pytest.mark.asyncio
async def test_lifespan_warns_when_admin_key_weak(
    caplog: pytest.LogCaptureFixture,
    tmp_path,
) -> None:
    """Startup emite warning (sem erro) quando a admin key é weak.

    Usa um MagicMock em vez de construir ``Settings`` real, já que a chave
    fraca seria rejeitada na construção (ADK-01). Isso isola o branch de
    warning do lifespan (ADK-03/BR2).
    """
    mock_settings = MagicMock()
    mock_settings.admin_api_key.get_secret_value.return_value = "dev-only"
    mock_settings.log_level = "INFO"
    mock_settings.log_dir = tmp_path / "logs"
    app = MagicMock()

    with (
        patch("medasist.api.main.get_settings", return_value=mock_settings),
        patch("medasist.api.main.get_client"),
        patch("medasist.api.main.build_embeddings"),
        patch("medasist.api.main.get_all_vectorstores", return_value={}),
        patch("medasist.api.main.build_chain"),
        caplog.at_level(logging.WARNING, logger="medasist.api.main"),
    ):
        async with lifespan(app):
            pass

    assert len(app.state.chains) == 4
    messages = "".join(r.message for r in caplog.records)
    assert "ADMIN_API_KEY" in messages
    assert str(ADMIN_KEY_MIN_LENGTH) in messages


@pytest.mark.asyncio
async def test_lifespan_no_warning_for_strong_key(
    caplog: pytest.LogCaptureFixture,
    tmp_path,
) -> None:
    """Startup NÃO emite warning quando a admin key é forte."""
    mock_settings = MagicMock()
    mock_settings.admin_api_key.get_secret_value.return_value = (
        "test-admin-key-0123456789"
    )
    mock_settings.log_level = "INFO"
    mock_settings.log_dir = tmp_path / "logs"
    app = MagicMock()

    with (
        patch("medasist.api.main.get_settings", return_value=mock_settings),
        patch("medasist.api.main.get_client"),
        patch("medasist.api.main.build_embeddings"),
        patch("medasist.api.main.get_all_vectorstores", return_value={}),
        patch("medasist.api.main.build_chain"),
        caplog.at_level(logging.WARNING, logger="medasist.api.main"),
    ):
        async with lifespan(app):
            pass

    assert not any(
        r.levelno == logging.WARNING and "ADMIN_API_KEY" in r.message
        for r in caplog.records
    )
