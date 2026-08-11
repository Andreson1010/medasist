from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

from pythonjsonlogger.json import JsonFormatter

logger = logging.getLogger(__name__)

_HANDLER_NAME_PREFIX = "medasist-"
_configured_apps: dict[str, bool] = {}
_lock = threading.Lock()


class _AppNameFilter(logging.Filter):
    """Injeta o campo ``app`` em todos os records do handler.

    O ``LogRecord`` padrão não possui o atributo ``app``; o filtro garante
    que todo registro gravado pelo handler JSON carregue a identificação
    da aplicação (ex: ``api``, ``ui``).

    Parameters
    ----------
    app_name : str
        Nome da aplicação a ser gravado no campo ``app``.
    """

    def __init__(self, app_name: str) -> None:
        super().__init__()
        self.app_name = app_name

    def filter(self, record: logging.LogRecord) -> bool:
        record.app = self.app_name
        return True


def configure_logging(settings: Any, app_name: str) -> Path:
    """Configura logging estruturado JSON para uma aplicação.

    Instala no logger raiz um ``FileHandler`` (JSON, UTF-8) e um
    ``StreamHandler`` (texto simples) no nível de ``settings.log_level``.
    Idempotente por ``app_name`` e thread-safe: chamadas repetidas (ex:
    re-execução do script da UI a cada interação) não duplicam handlers.
    Handlers são instalados sob lock; a flag de configurado só é setada
    após sucesso — falha em ``mkdir``/``FileHandler`` permite retry.

    Parameters
    ----------
    settings : Settings
        Configurações com ``log_dir`` e ``log_level`` (validado pelo Settings).
    app_name : str
        Nome da aplicação; define o arquivo ``<log_dir>/<app_name>.log``.

    Returns
    -------
    Path
        Caminho do arquivo de log criado.
    """
    with _lock:
        if _configured_apps.get(app_name):
            return settings.log_dir / f"{app_name}.log"

        log_dir = settings.log_dir
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / f"{app_name}.log"
        level = getattr(logging, settings.log_level)

        root = logging.getLogger()
        root.setLevel(level)

        _remove_previous_handlers(root, app_name)

        json_handler = logging.FileHandler(log_file, encoding="utf-8")
        json_handler.setLevel(level)
        json_handler.set_name(f"{_HANDLER_NAME_PREFIX}json-{app_name}")
        json_handler.setFormatter(
            JsonFormatter(
                "%(asctime)s %(levelname)s %(name)s %(message)s %(app)s",
                rename_fields={"name": "logger"},
                json_ensure_ascii=False,
                json_default=str,
            )
        )
        json_handler.addFilter(_AppNameFilter(app_name))

        stream_handler = logging.StreamHandler()
        stream_handler.setLevel(level)
        stream_handler.set_name(f"{_HANDLER_NAME_PREFIX}stream-{app_name}")
        stream_handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
        )

        root.addHandler(json_handler)
        root.addHandler(stream_handler)
        # Flag só após handlers instalados: se mkdir/FileHandler falhar,
        # a próxima chamada pode tentar de novo (ex: volume Docker não-gravável).
        _configured_apps[app_name] = True

        logger.info(
            "Logging estruturado configurado para app='%s' em %s", app_name, log_file
        )
        return log_file


def _remove_previous_handlers(root: logging.Logger, app_name: str) -> None:
    """Remove handlers previamente instalados para a mesma aplicação.

    Medida de segurança além do guard por ``app_name``: se o estado do módulo
    for resetado (ex: em testes) mas os handlers permanecerem no root, evita
    duplicação de handlers e de escrita no arquivo.

    Parameters
    ----------
    root : logging.Logger
        Logger raiz.
    app_name : str
        Nome da aplicação cujos handlers devem ser removidos.
    """
    prefixes = (
        f"{_HANDLER_NAME_PREFIX}json-{app_name}",
        f"{_HANDLER_NAME_PREFIX}stream-{app_name}",
    )
    for handler in list(root.handlers):
        if handler.get_name() in prefixes:
            handler.close()
            root.removeHandler(handler)
