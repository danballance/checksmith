"""Tests for :mod:`checksmith.runner`."""

import pytest

from checksmith.dtos import ExitCode
from checksmith.outputs.check_full import CheckFull
from checksmith.runner import Runner
from checksmith.tools.tool import Tool


@pytest.fixture
def tool() -> Tool:
    return Tool()


def test_runner_retains_the_tools_it_was_given(tool: Tool) -> None:
    tools = [tool]

    runner = Runner(tools=tools)

    assert runner.tools == tools


def test_check_returns_a_check_full_output() -> None:
    runner = Runner(tools=[])

    result = runner.check()

    assert isinstance(result, CheckFull)


def test_check_reports_an_empty_suite_while_tools_are_not_executed(
    tool: Tool,
) -> None:
    runner = Runner(tools=[tool])

    result = runner.check()

    assert result.results == ()
    assert result.exit_code is ExitCode.SUCCESS
