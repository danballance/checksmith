"""Tests for console rendering of Checksmith results."""

import io
import json
from collections.abc import Callable

import pytest
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from checksmith.adapters.driving.cli.rendering import render_output
from checksmith.domain.models import CheckResult, CheckStatus, ExitCode
from checksmith.domain.results import CheckOutput, CliOutput


def test_unknown_result_types_require_an_explicit_renderer() -> None:
    class UnsupportedOutput(CliOutput):
        @property
        def exit_code(self) -> ExitCode:
            return ExitCode.SUCCESS

    with pytest.raises(TypeError, match="No renderer for UnsupportedOutput"):
        render_output(UnsupportedOutput())


@pytest.fixture
def render() -> Callable[[CliOutput], str]:
    """Render an output through a width-pinned, colourless console."""

    def _render(output: CliOutput) -> str:
        buffer = io.StringIO()
        Console(file=buffer, width=200, no_color=True).print(render_output(output))
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
    assert isinstance(render_output(CheckOutput()), Table)


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


@pytest.mark.parametrize("width", [80, 100, 160])
def test_multiline_details_render_below_the_summary_at_terminal_widths(
    width: int,
) -> None:
    output = CheckOutput(
        results=(
            CheckResult(
                check_id="ruff",
                status=CheckStatus.PASSED,
                messages=("All checks passed.",),
            ),
            CheckResult(
                check_id="pyarchgraph-packages",
                status=CheckStatus.FAILED,
                messages=(
                    "Found two cycle groups.",
                    (
                        "Cycle group 1\n"
                        "Detail which stays readable outside the narrow summary cell"
                    ),
                    "Cycle group 2\napp -> service\nservice -> app",
                ),
            ),
        )
    )
    buffer = io.StringIO()

    Console(file=buffer, width=width, no_color=True).print(render_output(output))
    rendered = buffer.getvalue()

    assert "All checks passed." in rendered
    assert "Found two cycle groups." in rendered
    assert "pyarchgraph-packages | FAIL" in rendered
    assert "Detail which stays readable outside the narrow summary cell" in rendered
    assert "app -> service" in rendered
    assert "service -> app" in rendered
    assert rendered.index("Found two cycle groups.") < rendered.index("Cycle group 1")
    assert rendered.index("Cycle group 1") < rendered.index("Cycle group 2")


def test_detail_panels_preserve_first_message_remainders_and_block_spacing() -> None:
    output = CheckOutput(
        results=(
            CheckResult(
                check_id="architecture",
                status=CheckStatus.ERROR,
                messages=(
                    "Analysis incomplete.\nFirst detail\nSecond detail",
                    "Additional context",
                    "Final block\nLast detail",
                ),
            ),
        )
    )

    rendered = render_output(output)

    assert isinstance(rendered, Group)
    table, panel = rendered.renderables
    assert isinstance(table, Table)
    assert isinstance(panel, Panel)
    assert isinstance(panel.renderable, Text)
    assert panel.renderable.plain == (
        "First detail\nSecond detail\n\n"
        "Additional context\n\nFinal block\nLast detail"
    )
    assert isinstance(panel.title, Text)
    assert panel.title.plain == "architecture | ERROR"


def test_detail_panels_preserve_literal_check_names_and_diagnostics(
    render: Callable[[CliOutput], str],
) -> None:
    output = CheckOutput(
        results=(
            CheckResult(
                check_id="[red]architecture[/red]",
                status=CheckStatus.FAILED,
                messages=(
                    "[bold]Review imports[/bold]",
                    "[red]Cycle group[/red]\nsrc/[name].py:1:1",
                    "[link=https://example.com]Literal context[/link]",
                ),
            ),
        )
    )

    rendered = render(output)

    assert "[red]architecture[/red] | FAIL" in rendered
    assert all(
        line in rendered for message in output.results[0].messages
        for line in message.splitlines()
    )


def test_multiple_detail_panels_follow_check_order() -> None:
    output = CheckOutput(
        results=tuple(
            CheckResult(
                check_id=status.value,
                status=status,
                messages=("Summary", "First detail\nSecond detail"),
            )
            for status in CheckStatus
        )
    )

    rendered = render_output(output)

    assert isinstance(rendered, Group)
    assert isinstance(rendered.renderables[0], Table)
    panels = rendered.renderables[1:]
    assert all(isinstance(panel, Panel) for panel in panels)
    assert [
        panel.title.plain
        for panel in panels
        if isinstance(panel, Panel) and isinstance(panel.title, Text)
    ] == ["failed | FAIL", "error | ERROR"]


@pytest.mark.parametrize(
    "messages",
    [
        ("No error findings.", "Selected view\nAnalysis details"),
        ("No error findings.\nAnalysis details", "Additional detail"),
    ],
)
def test_passing_checks_show_only_the_summary_and_preserve_json_details(
    messages: tuple[str, ...], render: Callable[[CliOutput], str]
) -> None:
    output = CheckOutput(
        results=(
            CheckResult(
                check_id="pyarchgraph-modules",
                status=CheckStatus.PASSED,
                messages=messages,
            ),
        )
    )

    assert isinstance(render_output(output), Table)
    rendered = render(output)

    assert "pyarchgraph-modules" in rendered
    assert "PASS" in rendered
    assert "No error findings." in rendered
    assert "Selected view" not in rendered
    assert "Analysis details" not in rendered
    assert "Additional detail" not in rendered
    assert CheckOutput.model_validate_json(output.model_dump_json()) == output


def test_passing_checks_preserve_short_inline_notices(
    render: Callable[[CliOutput], str],
) -> None:
    output = CheckOutput(
        results=(
            CheckResult(
                check_id="pytest",
                status=CheckStatus.PASSED,
                messages=("No tests ran.", "No tests were collected; the check passes."),
            ),
        )
    )

    assert isinstance(render_output(output), Table)
    rendered = render(output)

    assert all(message in rendered for message in output.results[0].messages)


def test_skipped_checks_keep_multiline_explanations_in_the_table(
    render: Callable[[CliOutput], str],
) -> None:
    output = CheckOutput(
        results=(
            CheckResult(
                check_id="architecture",
                status=CheckStatus.SKIPPED,
                messages=("Missing prerequisite.\nRequired source directory: src",),
            ),
        )
    )

    assert isinstance(render_output(output), Table)
    rendered = render(output)

    assert "SKIP" in rendered
    assert "Missing prerequisite." in rendered
    assert "Required source directory: src" in rendered


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
