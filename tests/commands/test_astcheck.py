from pathlib import Path

import pytest

from astcheck.domain.models import (
    AnalysisError,
    AnalysisReport,
    Finding,
    PluginReport,
    SourceLocation,
)
from checksmith.commands.registry import CommandFactory
from checksmith.config import Check
from checksmith.dtos import CheckStatus, CommandName, PackageType
from checksmith.errors import CheckOutputError
from tests.conftest import FakeProcesses

CLEAN_REPORT = AnalysisReport(
    schema_version=1,
    status="complete",
    modules=("src/service.py",),
    plugins=(PluginReport(plugin_id="oopcheck", findings=()),),
    errors=(),
).model_dump_json()

FINDING_REPORT = AnalysisReport(
    schema_version=1,
    status="complete",
    modules=("src/service.py",),
    plugins=(
        PluginReport(
            plugin_id="oopcheck",
            findings=(
                Finding(
                    rule_id="repeated-collaborator",
                    message="Repository appears in three functions.",
                    location=SourceLocation(path="src/service.py", line=1, column=1),
                    related_locations=(
                        SourceLocation(path="src/service.py", line=6, column=1),
                        SourceLocation(path="src/service.py", line=12, column=5),
                    ),
                ),
                Finding(
                    rule_id="standalone-statement-share",
                    message="Standalone functions contain 100% of callable statements.",
                    location=SourceLocation(path="src/service.py", line=1, column=1),
                    related_locations=(),
                ),
            ),
        ),
    ),
    errors=(),
).model_dump_json()

ERROR_REPORT = AnalysisReport(
    schema_version=1,
    status="error",
    modules=(),
    plugins=(),
    errors=(
        AnalysisError(
            message="Cannot read configuration.",
            path=None,
            line=None,
            column=None,
        ),
    ),
).model_dump_json()


def test_command_runs_astcheck_from_the_checksmith_distribution(
    tmp_path: Path,
    processes: FakeProcesses,
    command_factory: CommandFactory,
) -> None:
    check = Check(
        id="custom-analysis",
        package_type=PackageType.UVX,
        package="checksmith==0.1.0",
        command=CommandName.ASTCHECK,
        args=("check", "--config", "astcheck.yaml", "--format", "json"),
    )
    processes.stdout = CLEAN_REPORT

    result = command_factory.for_name(name=CommandName.ASTCHECK).run(
        check=check, project_root=tmp_path
    )

    assert result.status is CheckStatus.PASSED
    assert processes.started[0].argv == (
        "uvx",
        "--from",
        "checksmith==0.1.0",
        "astcheck",
        "check",
        "--config",
        "astcheck.yaml",
        "--format",
        "json",
    )
    assert processes.started[0].cwd == tmp_path


def test_clean_analysis_passes(
    tmp_path: Path, command_factory: CommandFactory
) -> None:
    result = command_factory.for_name(name=CommandName.ASTCHECK).process_response(
        check_id="custom-analysis",
        project_root=tmp_path,
        exit_code=0,
        stdout=CLEAN_REPORT,
        stderr="",
    )

    assert result.check_id == "custom-analysis"
    assert result.status is CheckStatus.PASSED
    assert result.messages == (
        "Analysis is complete; modules: 1; plugins: 1; findings: 0.",
    )


def test_findings_fail_and_preserve_plugin_rule_and_related_locations(
    tmp_path: Path, command_factory: CommandFactory
) -> None:
    result = command_factory.for_name(name=CommandName.ASTCHECK).process_response(
        check_id="custom-analysis",
        project_root=tmp_path,
        exit_code=1,
        stdout=FINDING_REPORT,
        stderr="",
    )

    assert result.status is CheckStatus.FAILED
    assert result.messages == (
        "Analysis is complete; modules: 1; plugins: 1; findings: 2.",
        (
            "src/service.py:1:1: [oopcheck/repeated-collaborator] "
            "Repository appears in three functions. Related locations: "
            "src/service.py:6:1, src/service.py:12:5."
        ),
        (
            "src/service.py:1:1: [oopcheck/standalone-statement-share] "
            "Standalone functions contain 100% of callable statements."
        ),
    )


@pytest.mark.parametrize(
    ("path", "line", "column", "expected"),
    [
        (None, None, None, "Error: Invalid source."),
        ("src/broken.py", None, None, "src/broken.py: Error: Invalid source."),
        ("src/broken.py", 4, None, "src/broken.py:4: Error: Invalid source."),
        ("src/broken.py", 4, 7, "src/broken.py:4:7: Error: Invalid source."),
    ],
)
def test_execution_errors_have_error_status_and_available_location(
    path: str | None,
    line: int | None,
    column: int | None,
    expected: str,
    tmp_path: Path,
    command_factory: CommandFactory,
) -> None:
    report = AnalysisReport(
        schema_version=1,
        status="error",
        modules=(),
        plugins=(),
        errors=(
            AnalysisError(
                message="Invalid source.", path=path, line=line, column=column
            ),
        ),
    )
    result = command_factory.for_name(name=CommandName.ASTCHECK).process_response(
        check_id="custom-analysis",
        project_root=tmp_path,
        exit_code=2,
        stdout=report.model_dump_json(),
        stderr="",
    )

    assert result.status is CheckStatus.ERROR
    assert result.messages == (
        "Analysis is error; modules: 0; plugins: 0; findings: 0.",
        expected,
    )


@pytest.mark.parametrize(
    ("stdout", "exit_code"),
    [
        (CLEAN_REPORT, 1),
        (CLEAN_REPORT, 2),
        (FINDING_REPORT, 0),
        (FINDING_REPORT, 2),
        (ERROR_REPORT, 0),
        (ERROR_REPORT, 1),
    ],
)
def test_exit_code_must_agree_with_report(
    stdout: str,
    exit_code: int,
    tmp_path: Path,
    command_factory: CommandFactory,
) -> None:
    with pytest.raises(CheckOutputError, match="exit code disagrees"):
        command_factory.for_name(name=CommandName.ASTCHECK).process_response(
            check_id="custom-analysis",
            project_root=tmp_path,
            exit_code=exit_code,
            stdout=stdout,
            stderr="",
        )


@pytest.mark.parametrize("exit_code", [-9, 3, 127])
def test_unrecognized_exits_preserve_process_output(
    exit_code: int, tmp_path: Path, command_factory: CommandFactory
) -> None:
    with pytest.raises(CheckOutputError, match="command could not start"):
        command_factory.for_name(name=CommandName.ASTCHECK).process_response(
            check_id="custom-analysis",
            project_root=tmp_path,
            exit_code=exit_code,
            stdout="invalid output",
            stderr="command could not start",
        )


@pytest.mark.parametrize("exit_code", [0, 1, 2])
def test_missing_report_preserves_stderr(
    exit_code: int, tmp_path: Path, command_factory: CommandFactory
) -> None:
    with pytest.raises(CheckOutputError, match="configuration missing"):
        command_factory.for_name(name=CommandName.ASTCHECK).process_response(
            check_id="custom-analysis",
            project_root=tmp_path,
            exit_code=exit_code,
            stdout=" \n",
            stderr="configuration missing",
        )


@pytest.mark.parametrize(
    "stdout",
    [
        "not JSON",
        "[]",
        "{}",
        CLEAN_REPORT.replace('"schema_version":1', '"schema_version":2'),
        CLEAN_REPORT.replace('"schema_version":1', '"schema_version":true'),
        CLEAN_REPORT.replace('"complete"', '"error"'),
        CLEAN_REPORT.replace('"errors":[]', '"errors":[],"unexpected":1'),
        FINDING_REPORT.replace('"line":1,', '"line":0,'),
        FINDING_REPORT.replace('"column":1', '"column":"1"'),
        ERROR_REPORT.replace('"error"', '"complete"'),
    ],
)
def test_malformed_reports_are_rejected(
    stdout: str, tmp_path: Path, command_factory: CommandFactory
) -> None:
    with pytest.raises(CheckOutputError, match="schema 1 JSON report"):
        command_factory.for_name(name=CommandName.ASTCHECK).process_response(
            check_id="custom-analysis",
            project_root=tmp_path,
            exit_code=0,
            stdout=stdout,
            stderr="",
        )
