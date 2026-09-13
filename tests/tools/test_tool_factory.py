"""Tests for :mod:`checksmith.tools.tool_factory`."""

from checksmith.config import Config
from checksmith.tools.tool_factory import ToolFactory


def test_factory_retains_the_config_it_was_given(config: Config) -> None:
    factory = ToolFactory(config=config)

    assert factory.config is config


def test_get_tools_returns_no_tools_while_no_tools_are_implemented(
    config: Config,
) -> None:
    factory = ToolFactory(config=config)

    assert factory.get_tools() == []
