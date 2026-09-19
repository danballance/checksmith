"""Tests for :mod:`checksmith.runner`."""

import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from checksmith.commands.command import Command
from checksmith.config import Check
from checksmith.dtos import (
    CheckResult,
    CommandName,
    ErrorSeverity,
    ExitCode,
    PackageType,
)
from checksmith.errors import CheckExecutionError, CheckOutputError
from checksmith.runner import Runner
from tests.conftest import FakeProcesses

PROJECT_ROOT = Path("/workspace/project")


class RecordingCommand(Command):
    """A command that records what it was asked to run instead of running it.

    Execution is unimplemented, so a real command cannot be run. This one
    reports a result the test chose, which is what lets the runner's own
    pairing, collecting and ordering be exercised at all.
    """

    def __init__(self, results: Mapping[str, CheckResult | Exception]) -> None:
        self.results = results
        self.runs: list[str] = []
        """One check id per call, so a check run twice cannot pass unnoticed."""

        self.roots: list[Path] = []
        """One project root per call, alongside the id it was passed with."""

    @property
    def name(self) -> CommandName:
        return CommandName.RUFF

    def run(self, *, check: Check, project_root: Path) -> CheckResult:
        self.runs.append(check.id)
        self.roots.append(project_root)
        result = self.results[check.id]
        if isinstance(result, Exception):
            raise result
        return result

    def process_response(
        self,
        *,
        check_id: str,
        project_root: Path,
        exit_code: int,
        stdout: str,
        stderr: str,
    ) -> CheckResult:
        raise NotImplementedError


def ruff_check(*, check_id: str) -> Check:
    return Check(
        id=check_id,
        package_type=PackageType.UVX,
        package="ruff==0.16.7",
        command=CommandName.RUFF,
        args=("check", "."),
    )


@pytest.fixture
def checks() -> tuple[Check, ...]:
    """Two checks naming the one command --- two configurations of ruff."""
    return (ruff_check(check_id="lint"), ruff_check(check_id="format"))


def test_runner_retains_what_it_was_given(checks: tuple[Check, ...]) -> None:
    commands = Command.registry()

    runner = Runner(checks=checks, commands=commands, project_root=PROJECT_ROOT)

    assert runner.checks == checks
    assert runner.commands == commands
    assert runner.project_root == PROJECT_ROOT


def test_every_check_is_run_once_and_every_result_is_kept(
    checks: tuple[Check, ...],
) -> None:
    recorder = RecordingCommand(
        results={
            "lint": CheckResult(
                check_id="lint",
                severity=ErrorSeverity.FAILURE,
                messages=("app.py:1:1 F401 Unused import",),
            ),
            "format": CheckResult(check_id="format", severity=None, messages=()),
        }
    )

    output = Runner(
        checks=checks,
        commands={CommandName.RUFF: recorder},
        project_root=PROJECT_ROOT,
    ).check()

    assert tuple(result.check_id for result in output.results) == ("lint", "format")
    assert recorder.runs == ["lint", "format"]
    assert output.exit_code is ExitCode.UNHEALTHY


def test_two_checks_naming_one_command_share_the_single_handler(
    checks: tuple[Check, ...],
) -> None:
    """The point of the arrangement: a check configures a command, it is not one."""
    recorder = RecordingCommand(
        results={
            "lint": CheckResult(check_id="lint", severity=None, messages=()),
            "format": CheckResult(check_id="format", severity=None, messages=()),
        }
    )

    Runner(
        checks=checks,
        commands={CommandName.RUFF: recorder},
        project_root=PROJECT_ROOT,
    ).check()

    assert len(recorder.runs) == len(checks)


def test_every_check_runs_in_the_one_project_root(
    checks: tuple[Check, ...],
) -> None:
    """The root belongs to the config as a whole, not to any check in it."""
    recorder = RecordingCommand(
        results={
            "lint": CheckResult(check_id="lint", severity=None, messages=()),
            "format": CheckResult(check_id="format", severity=None, messages=()),
        }
    )

    Runner(
        checks=checks,
        commands={CommandName.RUFF: recorder},
        project_root=PROJECT_ROOT,
    ).check()

    assert recorder.roots == [PROJECT_ROOT, PROJECT_ROOT]


def test_unreadable_tool_reports_are_collected_for_every_check(
    checks: tuple[Check, ...],
    processes: FakeProcesses,
) -> None:
    output = Runner(
        checks=checks,
        commands=Command.registry(),
        project_root=PROJECT_ROOT,
    ).check()

    assert len(processes.started) == 2
    assert tuple(result.check_id for result in output.results) == ("lint", "format")
    assert all(result.severity is ErrorSeverity.ERROR for result in output.results)
    assert all(
        f"Check '{result.check_id}':" in result.messages[0]
        for result in output.results
    )
    assert output.exit_code is ExitCode.ERROR


def test_a_whole_suite_runs_through_the_commands_checksmith_ships(
    checks: tuple[Check, ...],
    processes: FakeProcesses,
) -> None:
    """Two real checks, read by the real command, over a project with nothing wrong."""
    processes.stdout = "[]"

    output = Runner(
        checks=checks,
        commands=Command.registry(),
        project_root=PROJECT_ROOT,
    ).check()

    assert len(processes.started) == 2
    assert output.results == (
        CheckResult(check_id="lint", severity=None, messages=()),
        CheckResult(check_id="format", severity=None, messages=()),
    )
    assert output.exit_code is ExitCode.SUCCESS


def test_a_run_with_no_checks_at_all_is_rejected() -> None:
    """The schema forbids an empty suite, so reaching here is a caller's bug."""
    with pytest.raises(ValueError, match="at least one check"):
        Runner(
            checks=(),
            commands=Command.registry(),
            project_root=PROJECT_ROOT,
        ).check()


def test_a_mixed_ruff_and_semgrep_suite_uses_each_commands_report_format(
    processes: FakeProcesses,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checks = (
        ruff_check(check_id="lint"),
        Check(
            id="function-style",
            package_type=PackageType.UVX,
            package="semgrep==1.176.1",
            command=CommandName.SEMGREP,
            args=("scan", "--json", "--config", ".checksmith/semgrep.yaml", "."),
        ),
    )

    def run(
        argv: Sequence[str],
        *,
        cwd: str,
        stdin: int,
        capture_output: bool,
        text: bool,
        encoding: str,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        reports = {
            "ruff": "[]",
            "semgrep": '{"results": [], "errors": []}',
        }
        processes.stdout = reports[argv[3]]
        return processes.run(
            argv,
            cwd=cwd,
            stdin=stdin,
            capture_output=capture_output,
            text=text,
            encoding=encoding,
            check=check,
        )

    monkeypatch.setattr(subprocess, "run", run)

    output = Runner(
        checks=checks,
        commands=Command.registry(),
        project_root=PROJECT_ROOT,
    ).check()

    assert tuple(process.argv[3] for process in processes.started) == (
        "ruff",
        "semgrep",
    )
    assert output.results == (
        CheckResult(check_id="lint", severity=None, messages=()),
        CheckResult(check_id="function-style", severity=None, messages=()),
    )
    assert output.exit_code is ExitCode.SUCCESS


@pytest.mark.parametrize(
    "error",
    [
        CheckExecutionError(
            check_id="broken",
            program="uvx",
            working_directory=PROJECT_ROOT,
            problem="No such file or directory",
        ),
        CheckOutputError(
            check_id="broken",
            command=CommandName.RUFF,
            summary="ruff could not scan",
            problem="invalid configuration",
        ),
    ],
)
def test_a_tool_error_preserves_prior_results_and_runs_later_checks(
    error: CheckExecutionError | CheckOutputError,
) -> None:
    passing = CheckResult(check_id="before", severity=None, messages=())
    failing = CheckResult(
        check_id="violations",
        severity=ErrorSeverity.FAILURE,
        messages=("app.py:1:1 F401 Unused import", "app.py:2:1 F821 Unknown name"),
    )
    later = CheckResult(check_id="after", severity=None, messages=())
    recorder = RecordingCommand(
        results={
            "before": passing,
            "violations": failing,
            "broken": error,
            "after": later,
        }
    )
    check_ids = ("before", "violations", "broken", "after")

    output = Runner(
        checks=tuple(ruff_check(check_id=check_id) for check_id in check_ids),
        commands={CommandName.RUFF: recorder},
        project_root=PROJECT_ROOT,
    ).check()

    assert recorder.runs == list(check_ids)
    assert output.results == (
        passing,
        failing,
        CheckResult(
            check_id="broken",
            severity=ErrorSeverity.ERROR,
            messages=(str(error),),
        ),
        later,
    )
    assert output.exit_code is ExitCode.ERROR


def test_unexpected_command_bugs_are_not_reported_as_tool_results(
    checks: tuple[Check, ...],
) -> None:
    recorder = RecordingCommand(
        results={
            "lint": RuntimeError("adapter bug"),
            "format": CheckResult(check_id="format", severity=None, messages=()),
        }
    )

    with pytest.raises(RuntimeError, match="adapter bug"):
        Runner(
            checks=checks,
            commands={CommandName.RUFF: recorder},
            project_root=PROJECT_ROOT,
        ).check()

    assert recorder.runs == ["lint"]
