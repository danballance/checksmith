"""Tests for :mod:`checksmith.runner`."""

import logging
import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path

import pytest

from checksmith.commands.command import Command
from checksmith.commands.registry import CommandFactory
from checksmith.config import Check
from checksmith.dtos import (
    CheckResult,
    CheckStatus,
    CommandName,
    ExitCode,
    PackageType,
)
from checksmith.errors import (
    CheckExecutionError,
    CheckOutputError,
    CheckPrerequisiteError,
)
from checksmith.runner import Runner
from tests.conftest import FakeProcesses

PROJECT_ROOT = Path("/workspace/project")


def test_importing_the_runner_does_not_load_command_implementations(
    imported_modules: Callable[[str], frozenset[str]],
) -> None:
    modules = imported_modules("checksmith.runner")

    assert not modules & {
        "checksmith.commands.registry",
        "checksmith.commands.import_linter",
        "checksmith.commands.pyarchgraph",
        "checksmith.commands.ruff",
        "checksmith.commands.semgrep",
        "checksmith.commands.ty",
    }


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


class EligibilityCommand(RecordingCommand):
    def __init__(
        self,
        *,
        results: Mapping[str, CheckResult | Exception],
        eligibility: Mapping[str, bool | Exception],
    ) -> None:
        super().__init__(results=results)
        self.eligibility = eligibility
        self.calls: list[tuple[str, Check, Path]] = []

    def check_is_runnable(self, *, check: Check, project_root: Path) -> bool:
        self.calls.append(("eligibility", check, project_root))
        result = self.eligibility[check.id]
        if isinstance(result, Exception):
            raise result
        return result

    def run(self, *, check: Check, project_root: Path) -> CheckResult:
        self.calls.append(("run", check, project_root))
        return super().run(check=check, project_root=project_root)


class PreparingCommand(EligibilityCommand):
    def prepare(self, *, check: Check, project_root: Path) -> CheckResult:
        self.calls.append(("prepare", check, project_root))
        return RecordingCommand.run(self, check=check, project_root=project_root)


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
    commands = CommandFactory.registry()

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
                status=CheckStatus.FAILED,
                messages=("app.py:1:1 F401 Unused import",),
            ),
            "format": CheckResult(
                check_id="format", status=CheckStatus.PASSED, messages=()
            ),
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
            "lint": CheckResult(
                check_id="lint", status=CheckStatus.PASSED, messages=()
            ),
            "format": CheckResult(
                check_id="format", status=CheckStatus.PASSED, messages=()
            ),
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
            "lint": CheckResult(
                check_id="lint", status=CheckStatus.PASSED, messages=()
            ),
            "format": CheckResult(
                check_id="format", status=CheckStatus.PASSED, messages=()
            ),
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
        commands=CommandFactory.registry(),
        project_root=PROJECT_ROOT,
    ).check()

    assert len(processes.started) == 2
    assert tuple(result.check_id for result in output.results) == ("lint", "format")
    assert all(result.status is CheckStatus.ERROR for result in output.results)
    assert all(
        f"Check '{result.check_id}':" in result.messages[0] for result in output.results
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
        commands=CommandFactory.registry(),
        project_root=PROJECT_ROOT,
    ).check()

    assert len(processes.started) == 2
    assert output.results == (
        CheckResult(check_id="lint", status=CheckStatus.PASSED, messages=()),
        CheckResult(check_id="format", status=CheckStatus.PASSED, messages=()),
    )
    assert output.exit_code is ExitCode.SUCCESS


def test_a_run_with_no_checks_at_all_is_rejected() -> None:
    """The schema forbids an empty suite, so reaching here is a caller's bug."""
    with pytest.raises(ValueError, match="at least one check"):
        Runner(
            checks=(),
            commands=CommandFactory.registry(),
            project_root=PROJECT_ROOT,
        ).check()


def test_a_mixed_suite_uses_each_commands_report_format(
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
        Check(
            id="types",
            package_type=PackageType.UVX,
            package="ty==0.0.80",
            command=CommandName.TY,
            args=("check", "--output-format", "gitlab", "--error-on-warning", "."),
        ),
    )

    def run(
        *,
        check_id: str,
        argv: tuple[str, ...],
        cwd: Path,
        heartbeat_interval_seconds: float,
    ) -> subprocess.CompletedProcess[str]:
        reports = {
            "ruff": "[]",
            "semgrep": '{"results": [], "errors": []}',
            "ty": "[]",
        }
        processes.stdout = reports[argv[3]]
        return processes.run(
            check_id=check_id,
            argv=argv,
            cwd=cwd,
            heartbeat_interval_seconds=heartbeat_interval_seconds,
        )

    monkeypatch.setattr("checksmith.commands.command.run_process", run)

    output = Runner(
        checks=checks,
        commands=CommandFactory.registry(),
        project_root=PROJECT_ROOT,
    ).check()

    assert tuple(process.argv[3] for process in processes.started) == (
        "ruff",
        "semgrep",
        "ty",
    )
    assert output.results == (
        CheckResult(check_id="lint", status=CheckStatus.PASSED, messages=()),
        CheckResult(check_id="function-style", status=CheckStatus.PASSED, messages=()),
        CheckResult(check_id="types", status=CheckStatus.PASSED, messages=()),
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
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG, logger="checksmith.runner")
    passing = CheckResult(check_id="before", status=CheckStatus.PASSED, messages=())
    failing = CheckResult(
        check_id="violations",
        status=CheckStatus.FAILED,
        messages=("app.py:1:1 F401 Unused import", "app.py:2:1 F821 Unknown name"),
    )
    later = CheckResult(check_id="after", status=CheckStatus.PASSED, messages=())
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
            status=CheckStatus.ERROR,
            messages=(str(error),),
        ),
        later,
    )
    assert output.exit_code is ExitCode.ERROR
    assert [text for text in caplog.messages if text.startswith("starting check")] == [
        f"starting check {index}/4: {check_id}"
        for index, check_id in enumerate(check_ids, start=1)
    ]
    assert caplog.messages.index("broken status=error messages=1") < (
        caplog.messages.index("starting check 4/4: after")
    )


def test_unexpected_command_bugs_are_not_reported_as_tool_results(
    checks: tuple[Check, ...],
) -> None:
    recorder = RecordingCommand(
        results={
            "lint": RuntimeError("adapter bug"),
            "format": CheckResult(
                check_id="format", status=CheckStatus.PASSED, messages=()
            ),
        }
    )

    with pytest.raises(RuntimeError, match="adapter bug"):
        Runner(
            checks=checks,
            commands={CommandName.RUFF: recorder},
            project_root=PROJECT_ROOT,
        ).check()

    assert recorder.runs == ["lint"]


def test_eligibility_is_checked_once_per_check_immediately_before_execution() -> None:
    before = ruff_check(check_id="before")
    skipped = ruff_check(check_id="skipped")
    after = ruff_check(check_id="after")
    passing = CheckResult(check_id="before", status=CheckStatus.PASSED, messages=())
    failing = CheckResult(
        check_id="after", status=CheckStatus.FAILED, messages=("unused import",)
    )
    recorder = EligibilityCommand(
        results={"before": passing, "after": failing},
        eligibility={"before": True, "skipped": False, "after": True},
    )

    output = Runner(
        checks=(before, skipped, after),
        commands={CommandName.RUFF: recorder},
        project_root=PROJECT_ROOT,
    ).check()

    assert recorder.calls == [
        ("eligibility", before, PROJECT_ROOT),
        ("run", before, PROJECT_ROOT),
        ("eligibility", skipped, PROJECT_ROOT),
        ("eligibility", after, PROJECT_ROOT),
        ("run", after, PROJECT_ROOT),
    ]
    assert output.results == (
        passing,
        CheckResult(
            check_id="skipped",
            status=CheckStatus.SKIPPED,
            messages=("Check is not applicable to this project.",),
        ),
        failing,
    )
    assert output.exit_code is ExitCode.UNHEALTHY


def test_non_runnable_checks_never_start_a_subprocess(
    checks: tuple[Check, ...],
    processes: FakeProcesses,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG, logger="checksmith")

    def not_runnable(self: Command, *, check: Check, project_root: Path) -> bool:
        return False

    monkeypatch.setattr(Command, "check_is_runnable", not_runnable)

    output = Runner(
        checks=checks,
        commands=CommandFactory.registry(),
        project_root=PROJECT_ROOT,
    ).check()

    assert processes.started == []
    assert tuple(result.check_id for result in output.results) == ("lint", "format")
    assert all(result.status is CheckStatus.SKIPPED for result in output.results)
    assert output.exit_code is ExitCode.SUCCESS
    assert "starting check 1/2: lint" in caplog.messages
    assert "lint status=skipped messages=1" in caplog.messages
    assert not any(
        "command=" in text or "started program=" in text for text in caplog.messages
    )


def test_a_prerequisite_error_preserves_results_and_runs_later_checks() -> None:
    before = ruff_check(check_id="before")
    broken = ruff_check(check_id="broken")
    after = ruff_check(check_id="after")
    passing = CheckResult(check_id="before", status=CheckStatus.PASSED, messages=())
    later = CheckResult(check_id="after", status=CheckStatus.PASSED, messages=())
    error = CheckPrerequisiteError(
        check_id="broken",
        command=CommandName.RUFF,
        config_file=PROJECT_ROOT / "pyproject.toml",
        problem="cannot read configuration",
    )
    recorder = EligibilityCommand(
        results={"before": passing, "after": later},
        eligibility={"before": True, "broken": error, "after": True},
    )

    output = Runner(
        checks=(before, broken, after),
        commands={CommandName.RUFF: recorder},
        project_root=PROJECT_ROOT,
    ).check()

    assert recorder.calls == [
        ("eligibility", before, PROJECT_ROOT),
        ("run", before, PROJECT_ROOT),
        ("eligibility", broken, PROJECT_ROOT),
        ("eligibility", after, PROJECT_ROOT),
        ("run", after, PROJECT_ROOT),
    ]
    assert output.results == (
        passing,
        CheckResult(
            check_id="broken", status=CheckStatus.ERROR, messages=(str(error),)
        ),
        later,
    )
    assert output.exit_code is ExitCode.ERROR


def test_an_unexpected_eligibility_bug_propagates_without_starting_the_check(
    checks: tuple[Check, ...],
) -> None:
    recorder = EligibilityCommand(
        results={},
        eligibility={"lint": RuntimeError("eligibility bug"), "format": True},
    )

    with pytest.raises(RuntimeError, match="eligibility bug"):
        Runner(
            checks=checks,
            commands={CommandName.RUFF: recorder},
            project_root=PROJECT_ROOT,
        ).check()

    assert recorder.calls == [("eligibility", checks[0], PROJECT_ROOT)]
    assert recorder.runs == []


def test_prepare_calls_each_hook_once_without_checking_normal_eligibility(
    checks: tuple[Check, ...],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG, logger="checksmith.runner")
    prepared = tuple(
        CheckResult(check_id=check.id, status=CheckStatus.PASSED, messages=())
        for check in checks
    )
    recorder = PreparingCommand(
        results={result.check_id: result for result in prepared},
        eligibility={},
    )

    output = Runner(
        checks=checks,
        commands={CommandName.RUFF: recorder},
        project_root=PROJECT_ROOT,
    ).prepare()

    assert recorder.calls == [("prepare", check, PROJECT_ROOT) for check in checks]
    assert output.results == prepared
    assert output.exit_code is ExitCode.SUCCESS
    assert "starting preparation 1/2: lint" in caplog.messages
    assert "starting preparation 2/2: format" in caplog.messages


def test_prepare_skips_commands_without_a_preparation_hook(
    checks: tuple[Check, ...],
    processes: FakeProcesses,
) -> None:
    output = Runner(
        checks=checks,
        commands=CommandFactory.registry(),
        project_root=PROJECT_ROOT,
    ).prepare()

    assert processes.started == []
    assert tuple(result.check_id for result in output.results) == ("lint", "format")
    assert all(result.status is CheckStatus.SKIPPED for result in output.results)
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
            summary="could not prepare",
            problem="invalid output",
        ),
        CheckPrerequisiteError(
            check_id="broken",
            command=CommandName.RUFF,
            config_file=PROJECT_ROOT / "pyproject.toml",
            problem="invalid configuration",
        ),
    ],
)
def test_preparation_errors_preserve_results_and_prepare_later_checks(
    error: CheckExecutionError | CheckOutputError | CheckPrerequisiteError,
) -> None:
    passing = CheckResult(check_id="before", status=CheckStatus.PASSED, messages=())
    later = CheckResult(check_id="after", status=CheckStatus.PASSED, messages=())
    checks = tuple(
        ruff_check(check_id=check_id) for check_id in ("before", "broken", "after")
    )
    recorder = PreparingCommand(
        results={"before": passing, "broken": error, "after": later},
        eligibility={},
    )

    output = Runner(
        checks=checks,
        commands={CommandName.RUFF: recorder},
        project_root=PROJECT_ROOT,
    ).prepare()

    assert recorder.calls == [("prepare", check, PROJECT_ROOT) for check in checks]
    assert output.results == (
        passing,
        CheckResult(
            check_id="broken", status=CheckStatus.ERROR, messages=(str(error),)
        ),
        later,
    )
    assert output.exit_code is ExitCode.ERROR


def test_unexpected_preparation_bugs_propagate(
    checks: tuple[Check, ...],
) -> None:
    recorder = PreparingCommand(
        results={"lint": RuntimeError("preparation bug")},
        eligibility={},
    )

    with pytest.raises(RuntimeError, match="preparation bug"):
        Runner(
            checks=checks,
            commands={CommandName.RUFF: recorder},
            project_root=PROJECT_ROOT,
        ).prepare()

    assert recorder.calls == [("prepare", checks[0], PROJECT_ROOT)]


def test_prepare_rejects_an_empty_suite() -> None:
    with pytest.raises(ValueError, match="at least one check"):
        Runner(
            checks=(),
            commands=CommandFactory.registry(),
            project_root=PROJECT_ROOT,
        ).prepare()
