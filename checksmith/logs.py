"""Debug logging configuration for the Checksmith CLI."""

import logging
from typing import Final

from rich.console import Console
from rich.logging import RichHandler

LOGGER_NAME: Final = "checksmith"
"""Package logger every module's own logger descends from."""

LOG_FORMAT: Final = "%(message)s"
"""Rich draws the level, time and location columns; the formatter supplies the rest."""


def configure_logging(*, debug: bool) -> None:
    """Point the ``checksmith`` logger at stderr at the level ``debug`` implies."""
    handler = RichHandler(
        console=Console(stderr=True),
        show_time=True,
        show_level=True,
        show_path=True,
        rich_tracebacks=True,
        markup=False,
    )
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.DEBUG if debug else logging.WARNING)
    logger.handlers = [handler]
    logger.propagate = False
