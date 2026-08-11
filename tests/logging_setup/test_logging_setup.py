from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from medasist.config import Settings
from medasist.logging_setup import _configured_apps, configure_logging

_ADMIN_KEY = "test-admin-key-0123456789"


def _strong_settings(log_dir) -> Settings:
    """Settings com admin key forte e log_dir definido pelo teste."""
    return Settings(
        admin_api_key=_ADMIN_KEY,
        log_dir=log_dir,
    )


def _log_records(log_file) -> list[dict]:
    """Lê e parseia todas as linhas JSON do arquivo de log."""
    lines = log_file.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines]


class TestConfigureLoggingHappyPath:
    def test_creates_json_log_file(self, tmp_path) -> None:
        log_dir = tmp_path / "logs"
        log_file = configure_logging(_strong_settings(log_dir), "api")

        assert log_file == log_dir / "api.log"
        assert log_file.exists()

    def test_writes_json_lines_with_expected_fields(self, tmp_path) -> None:
        log_file = configure_logging(_strong_settings(tmp_path / "logs"), "api")

        logging.getLogger("medasist.test.happy").info(
            "Bula do Zolatril %s consultada", "com sucesso"
        )

        records = _log_records(log_file)
        target = [
            r
            for r in records
            if r["message"] == "Bula do Zolatril com sucesso consultada"
        ]
        assert target, f"mensagem não encontrada em {records}"
        record = target[0]
        assert {"asctime", "levelname", "logger", "message", "app"} <= set(record)
        assert record["logger"] == "medasist.test.happy"
        assert record["levelname"] == "INFO"
        assert record["app"] == "api"

    def test_default_log_dir_is_used(self, tmp_path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        settings = Settings(admin_api_key=_ADMIN_KEY, log_level="INFO")

        log_file = configure_logging(settings, "ui")

        assert log_file == Path("logs") / "ui.log"
        assert (tmp_path / "logs" / "ui.log").exists()


class TestConfigureLoggingIdempotency:
    def test_second_call_does_not_duplicate_handlers(self, tmp_path) -> None:
        settings = _strong_settings(tmp_path / "logs")
        configure_logging(settings, "api")
        root = logging.getLogger()
        first_count = len(_medasist_handlers(root))

        log_file = configure_logging(settings, "api")

        assert log_file == tmp_path / "logs" / "api.log"
        assert len(_medasist_handlers(root)) == first_count

    def test_messages_not_duplicated_in_file(self, tmp_path) -> None:
        settings = _strong_settings(tmp_path / "logs")
        log_file = configure_logging(settings, "api")
        logger = logging.getLogger("medasist.test.idempotent")

        logger.info("primeira mensagem")
        configure_logging(settings, "api")
        logger.info("segunda mensagem")

        messages = [r["message"] for r in _log_records(log_file)]
        assert messages.count("primeira mensagem") == 1
        assert messages.count("segunda mensagem") == 1


class TestConfigureLoggingUnicode:
    def test_unicode_and_emoji_serialized_correctly(self, tmp_path) -> None:
        configure_logging(_strong_settings(tmp_path / "logs"), "api")

        logging.getLogger("medasist.test.unicode").info("Zolatril áéç ✓ 🏥")

        records = _log_records(tmp_path / "logs" / "api.log")
        assert any(r["message"] == "Zolatril áéç ✓ 🏥" for r in records)


class TestConfigureLoggingLevel:
    def test_level_from_settings_applied_to_handlers_and_root(self, tmp_path) -> None:
        settings = Settings(
            admin_api_key=_ADMIN_KEY,
            log_dir=tmp_path / "logs",
            log_level="WARNING",
        )
        configure_logging(settings, "api")
        logger = logging.getLogger("medasist.test.level")

        logger.info("deve ser filtrado")
        logger.warning("deve aparecer no arquivo")

        messages = [r["message"] for r in _log_records(tmp_path / "logs" / "api.log")]
        assert "deve aparecer no arquivo" in messages
        assert "deve ser filtrado" not in messages


class TestConfigureLoggingFailureRetry:
    def test_file_handler_failure_does_not_mark_configured(
        self, tmp_path, monkeypatch
    ) -> None:
        """Falha ao criar FileHandler não deixa a flag True (permite retry)."""
        settings = _strong_settings(tmp_path / "logs")

        def _boom(*_args, **_kwargs):
            raise OSError("Permission denied: volume ./logs não-gravável")

        monkeypatch.setattr(logging, "FileHandler", _boom)

        with pytest.raises(OSError, match="Permission denied"):
            configure_logging(settings, "api")

        assert not _configured_apps.get("api")
        assert _medasist_handlers(logging.getLogger()) == []

        monkeypatch.undo()

        log_file = configure_logging(settings, "api")

        assert log_file.exists()
        assert _configured_apps.get("api") is True


def _medasist_handlers(root: logging.Logger) -> list[logging.Handler]:
    """Handlers instalados por configure_logging no logger raiz."""
    from medasist.logging_setup import _HANDLER_NAME_PREFIX

    return [
        h
        for h in root.handlers
        if h.get_name() and h.get_name().startswith(_HANDLER_NAME_PREFIX)
    ]
