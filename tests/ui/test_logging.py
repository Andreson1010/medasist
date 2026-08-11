from __future__ import annotations

import json
import logging

from medasist.config import Settings
from medasist.logging_setup import configure_logging


class TestUILogging:
    def test_ui_log_file_created_with_json_line(self, tmp_path) -> None:
        """configure_logging(settings, 'ui') cria ui.log com linha JSON e app='ui'."""
        settings = Settings(
            admin_api_key="test-admin-key-0123456789",
            log_dir=tmp_path / "logs",
        )

        log_file = configure_logging(settings, "ui")

        assert log_file == tmp_path / "logs" / "ui.log"
        logging.getLogger("medasist.test.ui").info("UI MedAssist iniciada")

        records = [
            json.loads(line)
            for line in log_file.read_text(encoding="utf-8").splitlines()
        ]
        assert any(
            r["message"] == "UI MedAssist iniciada" and r["app"] == "ui"
            for r in records
        )
