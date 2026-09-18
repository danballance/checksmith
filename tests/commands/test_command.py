"""Tests for :mod:`checksmith.commands.command`."""

import subprocess
from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict

from checksmith.commands.command import (
    Command,
    RuffCommand,
)
from checksmith.config import Check
from checksmith.dtos import CheckResult, CommandName, PackageType
from checksmith.errors import CheckExecutionError
from tests.conftest import FakeProcesses

PROJECT_ROOT = Path("/workspace/project")


def ruff_check(*, check_id: str = "lint") -> Check:
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
        exit_code: int,
        stdout: str,
        stderr: str,
    ) -> CheckResult:
        self.read.append(
            ProcessOutput(
                check_id=check_id,
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
            )
        )
        return CheckResult(
            check_id=check_id,
            failed=exit_code != 0,
            findings=("read by the recorder",),
        )


def test_the_base_command_cannot_be_instantiated() -> None:
    """An ABC, so a program with no implementation is an error, never a silent pass."""
    with pytest.raises(TypeError):
        Command()  # type: ignore[abstract]


@pytest.mark.parametrize(
    ("command_name", "expected"),
    [
        (CommandName.RUFF, RuffCommand),
    ],
)
def test_the_configured_command_selects_the_class_that_handles_it(
    command_name: CommandName,
    expected: type[Command],
) -> None:
    """The ``command`` field is the whole of the dispatch; nothing else selects."""
    command = Command.for_name(name=command_name)

    assert isinstance(command, expected)
    assert command.name is command_name


def test_the_registry_covers_every_command_a_config_may_name() -> None:
    """Total by construction, so a check's ``command`` can never miss."""
    registry = Command.registry()

    assert set(registry) == set(CommandName)


def test_each_registered_command_answers_to_the_key_it_is_filed_under() -> None:
    """A command filed under the wrong key would run the wrong program."""
    registry = Command.registry()

    assert all(command.name is name for name, command in registry.items())


def test_a_command_holds_no_configuration_of_its_own() -> None:
    """Statelessness is the point: one instance serves every check that names it."""
    command = Command.for_name(name=CommandName.RUFF)

    assert vars(command) == {}


# Starting the process


def test_a_check_is_started_as_the_vector_it_resolves_to(
    processes: FakeProcesses,
) -> None:
    """A vector, never a command string: nothing reaches a shell to re-parse it."""
    RecordingCommand().run(check=ruff_check(), project_root=PROJECT_ROOT)

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
    RecordingCommand().run(check=ruff_check(), project_root=PROJECT_ROOT)

    assert processes.started[0].cwd == PROJECT_ROOT


def test_stdin_is_closed_so_a_tool_that_asks_a_question_cannot_hang(
    processes: FakeProcesses,
) -> None:
    """Nobody is watching a quality gate; a prompt must fail rather than wait."""
    RecordingCommand().run(check=ruff_check(), project_root=PROJECT_ROOT)

    assert processes.started[0].stdin == subprocess.DEVNULL


def test_a_program_that_cannot_be_started_names_the_check_that_wanted_it(
    processes: FakeProcesses,
) -> None:
    """An absent ``uvx`` says which program; only Checksmith can say which check."""
    processes.refusal = FileNotFoundError(2, "No such file or directory", "uvx")

    with pytest.raises(CheckExecutionError) as raised:
        RecordingCommand().run(check=ruff_check(), project_root=PROJECT_ROOT)

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
        command.run(check=ruff_check(), project_root=PROJECT_ROOT)

    assert command.read == []


# Reading what it returned


def test_what_the_process_returned_is_handed_to_the_code_that_reads_it(
    processes: FakeProcesses,
) -> None:
    """The whole of what a process leaves behind, under the id that asked for it."""
    processes.exit_code = 1
    processes.stdout = "ruff said something"
    processes.stderr = "and grumbled"
    command = RecordingCommand()

    command.run(check=ruff_check(), project_root=PROJECT_ROOT)

    assert command.read == [
        ProcessOutput(
            check_id="lint",
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

    result = command.run(check=ruff_check(), project_root=PROJECT_ROOT)

    assert result.failed is True


def test_the_result_of_reading_the_output_is_the_result_of_the_run(
    processes: FakeProcesses,
) -> None:
    """``run`` starts and collects; judging what came back is not its job."""
    result = RecordingCommand().run(check=ruff_check(), project_root=PROJECT_ROOT)

    assert result == CheckResult(
        check_id="lint",
        failed=False,
        findings=("read by the recorder",),
    )


def test_a_run_still_fails_loudly_because_no_output_can_be_read_yet(
    processes: FakeProcesses,
) -> None:
    """The process starts now; reading it is the part still missing.

    A passing result would report success for a check nobody has read.
    """
    command = Command.for_name(name=CommandName.RUFF)

    with pytest.raises(NotImplementedError) as raised:
        command.run(check=ruff_check(), project_root=PROJECT_ROOT)

    assert len(processes.started) == 1
    assert "lint" in str(raised.value)


def test_a_failed_run_names_the_check_it_was_given_rather_than_the_command(
    processes: FakeProcesses,
) -> None:
    """One command serves many checks, so only the argument can say which failed."""
    command = Command.for_name(name=CommandName.RUFF)

    with pytest.raises(NotImplementedError) as raised:
        command.run(check=ruff_check(check_id="second-lint"), project_root=PROJECT_ROOT)

    assert "second-lint" in str(raised.value)


@pytest.mark.parametrize(
    ("command_name", "program"),
    [
        (CommandName.RUFF, "ruff"),
    ],
)
def test_reading_output_fails_loudly_while_each_program_is_unimplemented(
    command_name: CommandName,
    program: str,
) -> None:
    """The seam exists; no program's output format is understood yet."""
    command = Command.for_name(name=command_name)

    with pytest.raises(NotImplementedError) as raised:
        command.process_response(
            check_id="lint",
            exit_code=1,
            stdout="",
            stderr="",
        )

    assert program in str(raised.value)
    assert "lint" in str(raised.value)
