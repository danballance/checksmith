"""Tests for :mod:`checksmith.runner`."""

from collections.abc import Mapping
from pathlib import Path

import pytest

from checksmith.commands.command import Command
from checksmith.config import Check
from checksmith.dtos import CheckResult, CommandName, ExitCode, PackageType
from checksmith.runner import Runner

PROJECT_ROOT = Path("/workspace/project")


class RecordingCommand(Command):
    """A command that records what it was asked to run instead of running it.

    Execution is unimplemented, so a real command cannot be run. This one
    reports a result the test chose, which is what lets the runner's own
    pairing, collecting and ordering be exercised at all.
    """

    def __init__(self, results: Mapping[str, CheckResult]) -> None:
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
        return self.results[check.id]

    def process_response(
        self,
        *,
        check_id: str,
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
            "lint": CheckResult(check_id="lint"),
            "format": CheckResult(check_id="format", failed=True),
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
            "lint": CheckResult(check_id="lint"),
            "format": CheckResult(check_id="format"),
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
            "lint": CheckResult(check_id="lint"),
            "format": CheckResult(check_id="format"),
        }
    )

    Runner(
        checks=checks,
        commands={CommandName.RUFF: recorder},
        project_root=PROJECT_ROOT,
    ).check()

    assert recorder.roots == [PROJECT_ROOT, PROJECT_ROOT]


def test_a_check_that_cannot_run_stops_the_whole_run(
    checks: tuple[Check, ...],
) -> None:
    """Execution is unimplemented, so this is how a real suite behaves today."""
    with pytest.raises(NotImplementedError) as raised:
        Runner(
            checks=checks,
            commands=Command.registry(),
            project_root=PROJECT_ROOT,
        ).check()

    assert "lint" in str(raised.value)


def test_a_run_with_no_checks_at_all_is_rejected() -> None:
    """The schema forbids an empty suite, so reaching here is a caller's bug."""
    with pytest.raises(ValueError, match="at least one check"):
        Runner(
            checks=(),
            commands=Command.registry(),
            project_root=PROJECT_ROOT,
        ).check()
