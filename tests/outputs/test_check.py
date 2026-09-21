"""Tests for :mod:`checksmith.outputs.check`."""

import io
import json
from collections.abc import Callable

import pytest
from rich.console import Console
from rich.table import Table

from checksmith.dtos import CheckResult, CheckStatus, ExitCode
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
        CheckResult(check_id="ruff", status=CheckStatus.PASSED, messages=()),
        CheckResult(check_id="ty", status=CheckStatus.PASSED, messages=()),
    )


@pytest.fixture
def results_with_one_failure() -> tuple[CheckResult, ...]:
    return (
        CheckResult(check_id="ruff", status=CheckStatus.PASSED, messages=()),
        CheckResult(
            check_id="ty",
            status=CheckStatus.FAILED,
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
    "statuses",
    [
        (CheckStatus.ERROR,),
        (CheckStatus.FAILED, CheckStatus.ERROR),
        (CheckStatus.ERROR, CheckStatus.FAILED),
        (CheckStatus.PASSED, CheckStatus.ERROR, CheckStatus.PASSED),
        (CheckStatus.SKIPPED, CheckStatus.ERROR),
        (CheckStatus.ERROR, CheckStatus.SKIPPED),
        tuple(CheckStatus),
    ],
)
def test_tool_errors_take_precedence_over_other_statuses(
    statuses: tuple[CheckStatus, ...],
) -> None:
    output = CheckOutput(
        results=tuple(
            CheckResult(
                check_id=f"check-{index}", status=status, messages=()
            )
            for index, status in enumerate(statuses)
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
                check_id="semgrep", status=CheckStatus.ERROR, messages=messages
            ),
        )
    )

    rendered = render(output)

    assert "semgrep" in rendered
    assert "ERROR" in rendered
    assert "Messages" in rendered
    assert all(message in rendered for message in messages)


@pytest.mark.parametrize("status", list(CheckStatus))
def test_json_round_trips_with_explicit_status_and_messages(
    status: CheckStatus,
) -> None:
    messages = (
        () if status is CheckStatus.PASSED else ("first diagnostic", "second diagnostic")
    )
    output = CheckOutput(
        results=(
            CheckResult(check_id="semgrep", status=status, messages=messages),
        )
    )

    encoded = output.model_dump_json()

    assert CheckOutput.model_validate_json(encoded) == output
    assert json.loads(encoded)["results"] == [
        {
            "check_id": "semgrep",
            "status": status.value,
            "messages": list(messages),
        }
    ]


@pytest.mark.parametrize(
    ("statuses", "expected_exit_code"),
    [
        ((CheckStatus.SKIPPED,), ExitCode.SUCCESS),
        ((CheckStatus.SKIPPED, CheckStatus.SKIPPED), ExitCode.SUCCESS),
        ((CheckStatus.PASSED, CheckStatus.SKIPPED), ExitCode.SUCCESS),
        ((CheckStatus.SKIPPED, CheckStatus.PASSED), ExitCode.SUCCESS),
        ((CheckStatus.FAILED, CheckStatus.SKIPPED), ExitCode.UNHEALTHY),
        ((CheckStatus.SKIPPED, CheckStatus.FAILED), ExitCode.UNHEALTHY),
    ],
)
def test_skipped_checks_do_not_change_the_suite_exit_code(
    statuses: tuple[CheckStatus, ...],
    expected_exit_code: ExitCode,
) -> None:
    output = CheckOutput(
        results=tuple(
            CheckResult(check_id=f"check-{index}", status=status, messages=())
            for index, status in enumerate(statuses)
        )
    )

    assert output.exit_code is expected_exit_code


def test_rendering_reports_skipped_checks_and_their_explanation(
    render: Callable[[CliOutput], str],
) -> None:
    output = CheckOutput(
        results=(
            CheckResult(
                check_id="dependencies",
                status=CheckStatus.SKIPPED,
                messages=("Check prerequisites are not met.",),
            ),
        )
    )

    rendered = render(output)

    assert "dependencies" in rendered
    assert "SKIP" in rendered
    assert "Check prerequisites are not met." in rendered
    assert "PASS" not in rendered


def test_json_preserves_order_across_all_result_statuses() -> None:
    output = CheckOutput(
        results=tuple(
            CheckResult(check_id=status.value, status=status, messages=())
            for status in CheckStatus
        )
    )

    encoded_results = json.loads(output.model_dump_json())["results"]

    assert [result["check_id"] for result in encoded_results] == [
        status.value for status in CheckStatus
    ]
