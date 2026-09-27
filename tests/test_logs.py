import logging
from io import StringIO

import pytest

from checksmith.logs import LoggingConfigurator


@pytest.mark.parametrize("debug", [False, True])
def test_logging_configuration_uses_injected_dependencies(debug: bool) -> None:
    logger = logging.Logger("isolated-checksmith-test")  # noqa: LOG001 -- isolated instance
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    original = logging.NullHandler()
    logger.addHandler(original)

    LoggingConfigurator(logger=logger, handler=handler).configure(debug=debug)
    logger.debug("details")
    logger.warning("warning")

    assert logger.handlers == [handler]
    assert logger.level == (logging.DEBUG if debug else logging.WARNING)
    assert logger.propagate is False
    assert stream.getvalue() == ("details\nwarning\n" if debug else "warning\n")


def test_reconfiguration_resets_level_without_duplicating_handlers() -> None:
    logger = logging.Logger("isolated-checksmith-test")  # noqa: LOG001 -- isolated instance
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    configuration = LoggingConfigurator(logger=logger, handler=handler)

    configuration.configure(debug=True)
    configuration.configure(debug=False)
    configuration.configure(debug=False)
    logger.debug("hidden")
    logger.warning("once")

    assert logger.handlers == [handler]
    assert stream.getvalue() == "once\n"
