"""Debug logging configuration for the Checksmith CLI."""

import logging
from typing import Final

LOGGER_NAME: Final = "checksmith"
"""Package logger every module's own logger descends from."""

LOG_FORMAT: Final = "%(message)s"
"""Rich draws the level, time and location columns; the formatter supplies the rest."""


class LoggingConfigurator:
    def __init__(self, logger: logging.Logger, handler: logging.Handler) -> None:
        self._logger = logger
        self._handler = handler

    def configure(self, *, debug: bool) -> None:
        self._handler.setFormatter(logging.Formatter(LOG_FORMAT))
        self._logger.setLevel(logging.DEBUG if debug else logging.WARNING)
        self._logger.handlers = [self._handler]
        self._logger.propagate = False
