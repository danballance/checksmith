"""Tests for :mod:`checksmith.tools.tool_factory`."""

from checksmith.config import Config
from checksmith.tools.tool_factory import ToolFactory, ToolSource


def test_factory_retains_the_config_it_was_given(resolved_config: Config) -> None:
    factory = ToolFactory(config=resolved_config)

    assert factory.config is resolved_config


def test_the_factory_is_a_tool_source(resolved_config: Config) -> None:
    """Checked statically: the annotation is the assertion."""
    source: ToolSource = ToolFactory(config=resolved_config)

    assert len(source.get_tools()) == len(resolved_config.checks)


def test_one_tool_is_built_for_each_configured_check(
    resolved_config: Config,
) -> None:
    tools = ToolFactory(config=resolved_config).get_tools()

    assert len(tools) == len(resolved_config.checks)


def test_tools_keep_the_order_the_config_declares(
    resolved_config: Config,
) -> None:
    tools = ToolFactory(config=resolved_config).get_tools()

    assert [tool.name for tool in tools] == ["ruff", "prettier"]


def test_each_tool_carries_its_own_runner_invocation(
    resolved_config: Config,
) -> None:
    ruff, prettier = ToolFactory(config=resolved_config).get_tools()

    assert ruff.spec.argv[:4] == ("uvx", "--from", "ruff==0.16.7", "ruff")
    assert prettier.spec.argv[:5] == (
        "npx",
        "--yes",
        "--package",
        "prettier@3.6.2",
        "prettier",
    )


def test_every_tool_runs_in_the_configured_project_root(
    resolved_config: Config,
) -> None:
    tools = ToolFactory(config=resolved_config).get_tools()

    assert {tool.spec.working_directory for tool in tools} == {
        resolved_config.project_root
    }
