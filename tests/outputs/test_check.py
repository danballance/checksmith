"""Tests for :mod:`checksmith.outputs.check`."""

import io
from collections.abc import Callable

import pytest
from rich.console import Console
from rich.table import Table

from checksmith.dtos import ExitCode, ToolResult
from checksmith.outputs.base import CliOutput
from checksmith.outputs.checkoutput import CheckOutput


@pytest.fixture
def render() -> Callable[[CliOutput], str]:
    """Render an output through a width-pinned, colourless console."""

    def _render(output: CliOutput) -> str:
        buffer = io.StringIO()
        Console(file=buffer, width=200, no_color=True).print(output)
        return buffer.getvalue()

    return _render


@pytest.fixture
def passing_results() -> tuple[ToolResult, ...]:
    return (ToolResult(name="ruff"), ToolResult(name="ty"))


@pytest.fixture
def results_with_one_failure() -> tuple[ToolResult, ...]:
    return (
        ToolResult(name="ruff"),
        ToolResult(name="ty", failed=True, findings=("x.py:1 bad type",)),
    )


def test_check_is_a_cli_output() -> None:
    assert isinstance(CheckOutput(), CliOutput)


def test_an_empty_suite_succeeds() -> None:
    assert CheckOutput().exit_code is ExitCode.SUCCESS


def test_a_suite_of_passing_tools_succeeds(
    passing_results: tuple[ToolResult, ...],
) -> None:
    output = CheckOutput(results=passing_results)

    assert output.exit_code is ExitCode.SUCCESS


def test_a_single_failing_tool_makes_the_suite_unhealthy(
    results_with_one_failure: tuple[ToolResult, ...],
) -> None:
    output = CheckOutput(results=results_with_one_failure)

    assert output.exit_code is ExitCode.UNHEALTHY


def test_rendering_produces_a_table() -> None:
    assert isinstance(CheckOutput().__rich__(), Table)


def test_rendering_reports_each_tool_and_its_findings(
    render: Callable[[CliOutput], str],
    results_with_one_failure: tuple[ToolResult, ...],
) -> None:
    output = CheckOutput(results=results_with_one_failure)

    rendered = render(output)

    assert "ruff" in rendered
    assert "PASS" in rendered
    assert "ty" in rendered
    assert "FAIL" in rendered
    assert "x.py:1 bad type" in rendered


def test_json_round_trips() -> None:
    output = CheckOutput(results=(ToolResult(name="ty", failed=True, findings=("bad",)),))

    assert CheckOutput.model_validate_json(output.model_dump_json()) == output
