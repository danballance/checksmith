"""Tests for :mod:`checksmith.commands.command`."""

import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict

from checksmith.commands.command import Command
from checksmith.commands.registry import CommandFactory
from checksmith.config import Check
from checksmith.dtos import CheckResult, CheckStatus, CommandName, PackageType
from checksmith.errors import CheckExecutionError, CheckOutputError
from tests.conftest import FakeProcesses

PROJECT_ROOT = Path("/workspace/project")


def test_importing_the_base_does_not_load_command_implementations(
    imported_modules: Callable[[str], frozenset[str]],
) -> None:
    modules = imported_modules("checksmith.commands.command")

    assert not modules & {
        "checksmith.commands.registry",
        "checksmith.commands.import_linter",
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

    def __init__(self) -> None:
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


def test_the_base_command_cannot_be_instantiated() -> None:
    """An ABC, so a program with no implementation is an error, never a silent pass."""
    with pytest.raises(TypeError):
        Command()  # type: ignore[abstract]


def test_a_command_is_runnable_without_overriding_the_hook(
    processes: FakeProcesses,
) -> None:
    command = RecordingCommand()

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
    processes: FakeProcesses,
) -> None:
    """A vector, never a command string: nothing reaches a shell to re-parse it."""
    RecordingCommand().run(check=ruff_check(check_id="lint"), project_root=PROJECT_ROOT)

    assert processes.started[0].argv == (
        "uvx",
        "--from",
        "ruff==0.16.7",
        "ruff",
        "check",
        ".",
    )


def test_a_check_is_started_in_the_project_root_it_was_given(
    processes: FakeProcesses,
) -> None:
    """The tool runs where the project is, not where the user happened to stand."""
    RecordingCommand().run(check=ruff_check(check_id="lint"), project_root=PROJECT_ROOT)

    assert processes.started[0].cwd == PROJECT_ROOT


def test_stdin_is_closed_so_a_tool_that_asks_a_question_cannot_hang(
    processes: FakeProcesses,
) -> None:
    """Nobody is watching a quality gate; a prompt must fail rather than wait."""
    RecordingCommand().run(check=ruff_check(check_id="lint"), project_root=PROJECT_ROOT)

    assert processes.started[0].stdin == subprocess.DEVNULL


def test_a_program_that_cannot_be_started_names_the_check_that_wanted_it(
    processes: FakeProcesses,
) -> None:
    """An absent ``uvx`` says which program; only Checksmith can say which check."""
    processes.refusal = FileNotFoundError(2, "No such file or directory", "uvx")

    with pytest.raises(CheckExecutionError) as raised:
        RecordingCommand().run(
            check=ruff_check(check_id="lint"), project_root=PROJECT_ROOT
        )

    assert raised.value.check_id == "lint"
    assert raised.value.program == "uvx"
    assert raised.value.working_directory == PROJECT_ROOT
    assert "uvx" in str(raised.value)
    assert "lint" in str(raised.value)


def test_nothing_is_read_when_nothing_ran(processes: FakeProcesses) -> None:
    """Reading the output of a process that never started would invent a result."""
    processes.refusal = FileNotFoundError(2, "No such file or directory", "uvx")
    command = RecordingCommand()

    with pytest.raises(CheckExecutionError):
        command.run(check=ruff_check(check_id="lint"), project_root=PROJECT_ROOT)

    assert command.read == []


@pytest.mark.parametrize(
    ("command_name", "package"),
    [
        (CommandName.RUFF, "ruff==0.16.7"),
        (CommandName.SEMGREP, "semgrep==1.176.1"),
        (CommandName.IMPORT_LINTER, "import-linter==2.15"),
        (CommandName.TY, "ty==0.0.80"),
    ],
)
def test_invalid_utf8_output_is_a_check_output_error(
    command_name: CommandName,
    package: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    decoding_error = UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")

    def refuse_decoding(
        argv: tuple[str, ...],
        *,
        cwd: str,
        stdin: int,
        capture_output: bool,
        text: bool,
        encoding: str,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        raise decoding_error

    monkeypatch.setattr(subprocess, "run", refuse_decoding)
    check = Check(
        id="encoding-check",
        package_type=PackageType.UVX,
        package=package,
        command=command_name,
        args=(),
    )

    with pytest.raises(CheckOutputError) as raised:
        CommandFactory.for_name(name=command_name).run(
            check=check, project_root=PROJECT_ROOT
        )

    assert raised.value.check_id == "encoding-check"
    assert raised.value.command is command_name
    assert raised.value.__cause__ is decoding_error
    assert str(decoding_error) in str(raised.value)
    assert f"{command_name.value} did not return UTF-8 output" in str(raised.value)


# Reading what it returned


def test_what_the_process_returned_is_handed_to_the_code_that_reads_it(
    processes: FakeProcesses,
) -> None:
    """The whole of what a process leaves behind, under the id that asked for it."""
    processes.exit_code = 1
    processes.stdout = "ruff said something"
    processes.stderr = "and grumbled"
    command = RecordingCommand()

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
    processes: FakeProcesses,
) -> None:
    """A linter exits nonzero for the ordinary reason that it found something."""
    processes.exit_code = 1
    command = RecordingCommand()

    result = command.run(check=ruff_check(check_id="lint"), project_root=PROJECT_ROOT)

    assert result.status is CheckStatus.FAILED


def test_the_result_of_reading_the_output_is_the_result_of_the_run(
    processes: FakeProcesses,
) -> None:
    """``run`` starts and collects; judging what came back is not its job."""
    result = RecordingCommand().run(
        check=ruff_check(check_id="lint"), project_root=PROJECT_ROOT
    )

    assert result == CheckResult(
        check_id="lint",
        status=CheckStatus.PASSED,
        messages=("read by the recorder",),
    )


def test_the_project_root_is_handed_to_the_code_that_reads_the_output(
    processes: FakeProcesses,
) -> None:
    """A tool reporting absolute paths needs something to be read against."""
    command = RecordingCommand()

    command.run(check=ruff_check(check_id="lint"), project_root=PROJECT_ROOT)

    assert command.read[0].project_root == PROJECT_ROOT
