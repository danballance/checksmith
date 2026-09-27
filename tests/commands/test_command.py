"""Tests for :mod:`checksmith.commands.command`."""

import logging
import shlex
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict

from checksmith.commands.command import Command
from checksmith.config import Check
from checksmith.dtos import CheckResult, CheckStatus, CommandName, PackageType
from checksmith.errors import CheckExecutionError, CheckOutputError
from checksmith.prerequisites import (
    LocalProjectFiles,
    UvPrerequisites,
    UvProjectPrerequisites,
)
from checksmith.processes import ProcessExecutor
from tests.conftest import FakeProcesses, make_command_factory

PROJECT_ROOT = Path("/workspace/project")


def test_importing_the_base_does_not_load_command_implementations(
    imported_modules: Callable[[str], frozenset[str]],
) -> None:
    modules = imported_modules("checksmith.commands.command")

    assert not modules & {
        "checksmith.commands.registry",
        "checksmith.commands.import_linter",
        "checksmith.commands.pyarchgraph",
        "checksmith.commands.ruff",
        "checksmith.commands.semgrep",
        "checksmith.commands.ty",
    }


def ruff_check(*, check_id: str) -> Check:
    """One configured check, of the kind :class:`RuffCommand` handles."""
    return Check(
        id=check_id,
        package_type=PackageType.UVX,
        package="ruff==0.16.7",
        command=CommandName.RUFF,
        args=("check", "."),
    )


class ProcessOutput(BaseModel):
    """Everything one call to ``process_response`` was handed."""

    model_config = ConfigDict(frozen=True)

    check_id: str
    project_root: Path
    exit_code: int
    stdout: str
    stderr: str


class RecordingCommand(Command):
    """A command that records the process output it was asked to read.

    Every ``process_response`` Checksmith ships is still a stub, so this is the
    only way to watch what ``run`` hands across that seam.
    """

    def __init__(
        self, executor: ProcessExecutor, uv_prerequisites: UvPrerequisites
    ) -> None:
        super().__init__(executor=executor, uv_prerequisites=uv_prerequisites)
        self.read: list[ProcessOutput] = []

    @property
    def name(self) -> CommandName:
        return CommandName.RUFF

    def process_response(
        self,
        *,
        check_id: str,
        project_root: Path,
        exit_code: int,
        stdout: str,
        stderr: str,
    ) -> CheckResult:
        self.read.append(
            ProcessOutput(
                check_id=check_id,
                project_root=project_root,
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
            )
        )
        return CheckResult(
            check_id=check_id,
            status=CheckStatus.FAILED if exit_code != 0 else CheckStatus.PASSED,
            messages=("read by the recorder",),
        )


@pytest.fixture
def recording_command(processes: FakeProcesses) -> RecordingCommand:
    return RecordingCommand(
        executor=processes,
        uv_prerequisites=UvProjectPrerequisites(
            environment={}, files=LocalProjectFiles()
        ),
    )


def test_the_base_command_cannot_be_instantiated(processes: FakeProcesses) -> None:
    """An ABC, so a program with no implementation is an error, never a silent pass."""
    with pytest.raises(TypeError):
        Command(
            executor=processes,
            uv_prerequisites=UvProjectPrerequisites(
                environment={}, files=LocalProjectFiles()
            ),
        )  # type: ignore[abstract]


def test_a_command_is_runnable_without_overriding_the_hook(
    recording_command: RecordingCommand,
    processes: FakeProcesses,
) -> None:
    command = recording_command

    assert (
        command.check_is_runnable(
            check=ruff_check(check_id="lint"), project_root=PROJECT_ROOT
        )
        is True
    )
    assert processes.started == []
    assert command.read == []


# Starting the process


def test_a_check_is_started_as_the_vector_it_resolves_to(
    recording_command: RecordingCommand,
    processes: FakeProcesses,
) -> None:
    """A vector, never a command string: nothing reaches a shell to re-parse it."""
    recording_command.run(check=ruff_check(check_id="lint"), project_root=PROJECT_ROOT)

    assert processes.started[0].argv == (
        "uvx",
        "--from",
        "ruff==0.16.7",
        "ruff",
        "check",
        ".",
    )


def test_a_check_is_started_in_the_project_root_it_was_given(
    recording_command: RecordingCommand,
    processes: FakeProcesses,
) -> None:
    """The tool runs where the project is, not where the user happened to stand."""
    recording_command.run(check=ruff_check(check_id="lint"), project_root=PROJECT_ROOT)

    assert processes.started[0].cwd == PROJECT_ROOT


def test_process_diagnostics_receive_the_check_id_and_reporting_interval(
    recording_command: RecordingCommand,
    processes: FakeProcesses,
) -> None:
    recording_command.run(check=ruff_check(check_id="lint"), project_root=PROJECT_ROOT)

    assert processes.started[0].check_id == "lint"
    assert processes.started[0].heartbeat_interval_seconds == 10.0


def test_both_command_formats_are_logged_before_launch(
    processes: FakeProcesses,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG, logger="checksmith.commands.command")
    project_root = Path("/workspace/project with spaces")
    check = ruff_check(check_id="lint").model_copy(
        update={"args": ("check", "src/two words.py", "quote'", "", "$(literal)")}
    )

    class ObservingExecutor:
        def run(
            self,
            *,
            check_id: str,
            argv: tuple[str, ...],
            cwd: Path,
            heartbeat_interval_seconds: float,
        ) -> subprocess.CompletedProcess[str]:
            assert f"lint argv={argv} cwd={cwd}" in caplog.messages
            message = next(
                text for text in caplog.messages if text.startswith("lint command=")
            )
            rendered = message.removeprefix("lint command=").removesuffix(f" cwd={cwd}")
            assert tuple(shlex.split(rendered)) == argv
            assert not processes.started
            return processes.run(
                check_id=check_id,
                argv=argv,
                cwd=cwd,
                heartbeat_interval_seconds=heartbeat_interval_seconds,
            )

    command = RecordingCommand(
        executor=ObservingExecutor(),
        uv_prerequisites=UvProjectPrerequisites(
            environment={}, files=LocalProjectFiles()
        ),
    )

    command.run(check=check, project_root=project_root)

    assert len(processes.started) == 1
    assert caplog.messages[-1] == "lint processing output"


def test_a_program_that_cannot_be_started_names_the_check_that_wanted_it(
    recording_command: RecordingCommand,
    processes: FakeProcesses,
) -> None:
    """An absent ``uvx`` says which program; only Checksmith can say which check."""
    processes.refusal = FileNotFoundError(2, "No such file or directory", "uvx")

    with pytest.raises(CheckExecutionError) as raised:
        recording_command.run(
            check=ruff_check(check_id="lint"), project_root=PROJECT_ROOT
        )

    assert raised.value.check_id == "lint"
    assert raised.value.program == "uvx"
    assert raised.value.working_directory == PROJECT_ROOT
    assert "uvx" in str(raised.value)
    assert "lint" in str(raised.value)


def test_nothing_is_read_when_nothing_ran(
    recording_command: RecordingCommand, processes: FakeProcesses
) -> None:
    """Reading the output of a process that never started would invent a result."""
    processes.refusal = FileNotFoundError(2, "No such file or directory", "uvx")
    command = recording_command

    with pytest.raises(CheckExecutionError):
        command.run(check=ruff_check(check_id="lint"), project_root=PROJECT_ROOT)

    assert command.read == []


@pytest.mark.parametrize(
    ("command_name", "package", "arguments"),
    [
        (CommandName.COMPLEXIPY, "complexipy==8.0.1", ("--plain", "--failed")),
        (CommandName.RUFF, "ruff==0.16.7", ()),
        (CommandName.SEMGREP, "semgrep==1.176.1", ()),
        (CommandName.IMPORT_LINTER, "import-linter==2.15", ()),
        (CommandName.TY, "ty==0.0.80", ()),
    ],
)
def test_invalid_utf8_output_is_a_check_output_error(
    command_name: CommandName,
    package: str,
    arguments: tuple[str, ...],
) -> None:
    decoding_error = UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")

    class DecodingExecutor:
        def run(
            self,
            *,
            check_id: str,
            argv: tuple[str, ...],
            cwd: Path,
            heartbeat_interval_seconds: float,
        ) -> subprocess.CompletedProcess[str]:
            raise decoding_error

    command_factory = make_command_factory(executor=DecodingExecutor())
    check = Check(
        id="encoding-check",
        package_type=PackageType.UVX,
        package=package,
        command=command_name,
        args=arguments,
    )

    with pytest.raises(CheckOutputError) as raised:
        command_factory.for_name(name=command_name).run(
            check=check, project_root=PROJECT_ROOT
        )

    assert raised.value.check_id == "encoding-check"
    assert raised.value.command is command_name
    assert raised.value.__cause__ is decoding_error
    assert str(decoding_error) in str(raised.value)
    assert f"{command_name.value} did not return UTF-8 output" in str(raised.value)


# Reading what it returned


def test_what_the_process_returned_is_handed_to_the_code_that_reads_it(
    recording_command: RecordingCommand,
    processes: FakeProcesses,
) -> None:
    """The whole of what a process leaves behind, under the id that asked for it."""
    processes.exit_code = 1
    processes.stdout = "ruff said something"
    processes.stderr = "and grumbled"
    command = recording_command

    command.run(check=ruff_check(check_id="lint"), project_root=PROJECT_ROOT)

    assert command.read == [
        ProcessOutput(
            check_id="lint",
            project_root=PROJECT_ROOT,
            exit_code=1,
            stdout="ruff said something",
            stderr="and grumbled",
        )
    ]


def test_a_nonzero_exit_is_read_rather_than_raised(
    recording_command: RecordingCommand,
    processes: FakeProcesses,
) -> None:
    """A linter exits nonzero for the ordinary reason that it found something."""
    processes.exit_code = 1
    command = recording_command

    result = command.run(check=ruff_check(check_id="lint"), project_root=PROJECT_ROOT)

    assert result.status is CheckStatus.FAILED


def test_the_result_of_reading_the_output_is_the_result_of_the_run(
    recording_command: RecordingCommand,
    processes: FakeProcesses,
) -> None:
    """``run`` starts and collects; judging what came back is not its job."""
    result = recording_command.run(
        check=ruff_check(check_id="lint"), project_root=PROJECT_ROOT
    )

    assert result == CheckResult(
        check_id="lint",
        status=CheckStatus.PASSED,
        messages=("read by the recorder",),
    )


def test_the_project_root_is_handed_to_the_code_that_reads_the_output(
    recording_command: RecordingCommand,
    processes: FakeProcesses,
) -> None:
    """A tool reporting absolute paths needs something to be read against."""
    command = recording_command

    command.run(check=ruff_check(check_id="lint"), project_root=PROJECT_ROOT)

    assert command.read[0].project_root == PROJECT_ROOT
