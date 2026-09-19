"""Tests for :mod:`checksmith.outputs.check`."""

import io
import json
from collections.abc import Callable

import pytest
from rich.console import Console
from rich.table import Table

from checksmith.dtos import CheckResult, ErrorSeverity, ExitCode
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
def passing_results() -> tuple[CheckResult, ...]:
    return (
        CheckResult(check_id="ruff", severity=None, messages=()),
        CheckResult(check_id="ty", severity=None, messages=()),
    )


@pytest.fixture
def results_with_one_failure() -> tuple[CheckResult, ...]:
    return (
        CheckResult(check_id="ruff", severity=None, messages=()),
        CheckResult(
            check_id="ty",
            severity=ErrorSeverity.FAILURE,
            messages=("x.py:1 bad type",),
        ),
    )


def test_check_is_a_cli_output() -> None:
    assert isinstance(CheckOutput(), CliOutput)


def test_an_empty_suite_succeeds() -> None:
    assert CheckOutput().exit_code is ExitCode.SUCCESS


def test_a_suite_of_passing_checks_succeeds(
    passing_results: tuple[CheckResult, ...],
) -> None:
    output = CheckOutput(results=passing_results)

    assert output.exit_code is ExitCode.SUCCESS


def test_a_single_failing_check_makes_the_suite_unhealthy(
    results_with_one_failure: tuple[CheckResult, ...],
) -> None:
    output = CheckOutput(results=results_with_one_failure)

    assert output.exit_code is ExitCode.UNHEALTHY


@pytest.mark.parametrize(
    "severities",
    [
        (ErrorSeverity.ERROR,),
        (ErrorSeverity.FAILURE, ErrorSeverity.ERROR),
        (ErrorSeverity.ERROR, ErrorSeverity.FAILURE),
        (None, ErrorSeverity.ERROR, None),
    ],
)
def test_tool_errors_take_precedence_over_failures_and_passing_checks(
    severities: tuple[ErrorSeverity | None, ...],
) -> None:
    output = CheckOutput(
        results=tuple(
            CheckResult(
                check_id=f"check-{index}", severity=severity, messages=()
            )
            for index, severity in enumerate(severities)
        )
    )

    assert output.exit_code is ExitCode.ERROR


def test_rendering_produces_a_table() -> None:
    assert isinstance(CheckOutput().__rich__(), Table)


def test_rendering_reports_each_check_and_its_messages(
    render: Callable[[CliOutput], str],
    results_with_one_failure: tuple[CheckResult, ...],
) -> None:
    output = CheckOutput(results=results_with_one_failure)

    rendered = render(output)

    assert "ruff" in rendered
    assert "PASS" in rendered
    assert "ty" in rendered
    assert "FAIL" in rendered
    assert "x.py:1 bad type" in rendered
    assert "Messages" in rendered
    assert "Findings" not in rendered


def test_rendering_reports_errors_and_preserves_literal_diagnostics(
    render: Callable[[CliOutput], str],
) -> None:
    messages = ("[red]Could not parse[/red]", "src/[name].py:1 missing syntax")
    output = CheckOutput(
        results=(
            CheckResult(
                check_id="semgrep", severity=ErrorSeverity.ERROR, messages=messages
            ),
        )
    )

    rendered = render(output)

    assert "semgrep" in rendered
    assert "ERROR" in rendered
    assert "Messages" in rendered
    assert all(message in rendered for message in messages)


@pytest.mark.parametrize(
    "severity", [None, ErrorSeverity.FAILURE, ErrorSeverity.ERROR]
)
def test_json_round_trips_with_explicit_severity_and_messages(
    severity: ErrorSeverity | None,
) -> None:
    messages = () if severity is None else ("first diagnostic", "second diagnostic")
    output = CheckOutput(
        results=(
            CheckResult(check_id="semgrep", severity=severity, messages=messages),
        )
    )

    encoded = output.model_dump_json()

    assert CheckOutput.model_validate_json(encoded) == output
    assert json.loads(encoded)["results"] == [
        {
            "check_id": "semgrep",
            "severity": None if severity is None else severity.value,
            "messages": list(messages),
        }
    ]
