import pytest

from astcheck.adapters.presentation import OutputFormat, ReportPresenter
from astcheck.domain.models import (
    AnalysisError,
    AnalysisReport,
    Finding,
    PluginReport,
    SourceLocation,
)


class CapturedOutput:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def write(self, *, text: str) -> None:
        self.messages.append(text)


def test_json_output_is_one_valid_report_without_extra_text() -> None:
    report = AnalysisReport(
        schema_version=1,
        status="error",
        modules=(),
        plugins=(),
        errors=(
            AnalysisError(message="Invalid config.", path=None, line=None, column=None),
        ),
    )
    output = CapturedOutput()

    ReportPresenter(output=output).emit(report=report, format=OutputFormat.JSON)

    assert len(output.messages) == 1
    assert AnalysisReport.model_validate_json(output.messages[0]) == report


def test_text_output_identifies_plugin_rule_and_related_locations() -> None:
    report = AnalysisReport(
        schema_version=1,
        status="complete",
        modules=("app.py",),
        plugins=(
            PluginReport(
                plugin_id="custom",
                findings=(
                    Finding(
                        rule_id="cohesion",
                        message="Review these functions.",
                        location=SourceLocation(path="app.py", line=1, column=2),
                        related_locations=(
                            SourceLocation(path="app.py", line=5, column=1),
                        ),
                    ),
                ),
            ),
        ),
        errors=(),
    )
    output = CapturedOutput()

    ReportPresenter(output=output).emit(report=report, format=OutputFormat.TEXT)

    assert output.messages == [
        "app.py:1:2: custom/cohesion: Review these functions.",
        "  related: app.py:5:1",
        "Analyzed 1 modules; 1 findings.",
    ]


def test_clean_text_output_reports_zero_findings() -> None:
    report = AnalysisReport(
        schema_version=1,
        status="complete",
        modules=("app.py",),
        plugins=(PluginReport(plugin_id="custom", findings=()),),
        errors=(),
    )
    output = CapturedOutput()

    ReportPresenter(output=output).emit(report=report, format=OutputFormat.TEXT)

    assert output.messages == ["Analyzed 1 modules; 0 findings."]


@pytest.mark.parametrize(
    ("path", "line", "column", "prefix"),
    [
        (None, None, None, ""),
        ("app.py", None, None, "app.py: "),
        ("app.py", 2, None, "app.py:2: "),
        ("app.py", 2, 3, "app.py:2:3: "),
    ],
)
def test_error_text_includes_only_available_location_fields(
    path: str | None, line: int | None, column: int | None, prefix: str
) -> None:
    report = AnalysisReport(
        schema_version=1,
        status="error",
        modules=(),
        plugins=(),
        errors=(
            AnalysisError(message="Cannot analyze.", path=path, line=line, column=column),
        ),
    )
    output = CapturedOutput()

    ReportPresenter(output=output).emit(report=report, format=OutputFormat.TEXT)

    assert output.messages == [f"{prefix}astcheck error: Cannot analyze."]
