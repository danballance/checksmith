import ast
from pathlib import Path

import pytest
from pydantic import ValidationError

from astcheck.domain.errors import AstcheckError
from astcheck.domain.models import (
    AnalysisError,
    AnalysisModule,
    AnalysisProject,
    AnalysisReport,
    Finding,
    PluginReport,
    SourceLocation,
)

CLEAN_REPORT = AnalysisReport(
    schema_version=1,
    status="complete",
    modules=("app.py",),
    plugins=(PluginReport(plugin_id="sample", findings=()),),
    errors=(),
)


def test_project_snapshot_preserves_source_and_ast(tmp_path: Path) -> None:
    tree = ast.parse("value = 1\n")
    module = AnalysisModule(path="app.py", source="value = 1\n", tree=tree)
    project = AnalysisProject(root=tmp_path, modules=(module,))

    assert project.modules[0].tree is tree
    assert project.modules[0].source == "value = 1\n"
    with pytest.raises(ValidationError, match="frozen"):
        setattr(module, "path", "other.py")  # noqa: B010


@pytest.mark.parametrize(
    "json_value",
    [
        '{"path":"app.py","line":0,"column":1}',
        '{"path":"app.py","line":1,"column":0}',
        '{"path":"app.py","line":true,"column":1}',
        '{"path":"app.py","line":1,"column":"1"}',
        '{"path":"","line":1,"column":1}',
        '{"path":1,"line":1,"column":1}',
        '{"path":"app.py","line":1,"column":1,"extra":1}',
    ],
)
def test_source_locations_have_strict_one_based_positions(json_value: str) -> None:
    with pytest.raises(ValidationError):
        SourceLocation.model_validate_json(json_value)


def test_clean_report_round_trips_and_exits_zero() -> None:
    assert CLEAN_REPORT.exit_code == 0
    assert (
        AnalysisReport.model_validate_json(CLEAN_REPORT.model_dump_json())
        == CLEAN_REPORT
    )


def test_findings_exit_one_and_errors_take_precedence() -> None:
    location = SourceLocation(path="app.py", line=1, column=1)
    plugin = PluginReport(
        plugin_id="sample",
        findings=(
            Finding(
                rule_id="test-rule",
                message="Review this module.",
                location=location,
                related_locations=(location,),
            ),
        ),
    )
    report = AnalysisReport(
        schema_version=1,
        status="complete",
        modules=("app.py",),
        plugins=(plugin,),
        errors=(),
    )
    partial = AnalysisReport(
        schema_version=1,
        status="error",
        modules=("app.py",),
        plugins=(plugin,),
        errors=(
            AnalysisError(
                message="Other plugin failed.", path=None, line=None, column=None
            ),
        ),
    )

    assert report.exit_code == 1
    assert partial.exit_code == 2


@pytest.mark.parametrize(
    ("old", "replacement", "message"),
    [
        ('"schema_version":1', '"schema_version":true', "must be an integer"),
        ('"schema_version":1', '"schema_version":"1"', "must be an integer"),
        ('"schema_version":1', '"schema_version":1.0', "must be an integer"),
        ('"schema_version":1', '"schema_version":2', "Input should be 1"),
        ('"complete"', '"error"', "at least one error"),
        ('"modules":["app.py"]', '"modules":[]', "require modules and plugins"),
        (
            '"plugins":[{"plugin_id":"sample","findings":[]}]',
            '"plugins":[]',
            "require modules and plugins",
        ),
        (
            '"modules":["app.py"]',
            '"modules":["app.py","app.py"]',
            "module paths must be unique",
        ),
        (
            '{"plugin_id":"sample","findings":[]}',
            (
                '{"plugin_id":"sample","findings":[]},'
                '{"plugin_id":"sample","findings":[]}'
            ),
            "plugin IDs must be unique",
        ),
        (
            '"errors":[]',
            '"errors":[{"message":"Failure","path":null,"line":null,"column":null}]',
            "require modules and plugins, and no errors",
        ),
    ],
)
def test_contradictory_reports_are_rejected(
    old: str, replacement: str, message: str
) -> None:
    serialized = CLEAN_REPORT.model_dump_json().replace(old, replacement)

    with pytest.raises(ValidationError, match=message):
        AnalysisReport.model_validate_json(serialized)


@pytest.mark.parametrize("related", [False, True])
def test_findings_cannot_reference_unanalyzed_modules(related: bool) -> None:
    known = SourceLocation(path="app.py", line=1, column=1)
    unknown = SourceLocation(path="unknown.py", line=1, column=1)
    finding = Finding(
        rule_id="test-rule",
        message="Review module.",
        location=known if related else unknown,
        related_locations=(unknown,) if related else (),
    )

    with pytest.raises(ValidationError, match="unanalyzed source 'unknown.py'"):
        AnalysisReport(
            schema_version=1,
            status="complete",
            modules=("app.py",),
            plugins=(PluginReport(plugin_id="sample", findings=(finding,)),),
            errors=(),
        )


def test_analysis_error_preserves_its_source_location() -> None:
    location = SourceLocation(path="app.py", line=4, column=2)
    error = AstcheckError("Cannot parse source.", location)

    assert str(error) == "Cannot parse source."
    assert error.location is location
