"""Tests for :mod:`checksmith.commands.command`."""

from pathlib import Path

import pytest

from checksmith.commands.command import (
    Command,
    RuffCommand,
)
from checksmith.config import Check
from checksmith.dtos import CommandName, PackageType

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


def test_running_fails_loudly_while_execution_is_unimplemented() -> None:
    """A passing result would report success for a check that never ran."""
    command = Command.for_name(name=CommandName.RUFF)

    with pytest.raises(NotImplementedError) as raised:
        command.run(check=ruff_check(), project_root=PROJECT_ROOT)

    assert "lint" in str(raised.value)


def test_a_failed_run_names_the_check_it_was_given_rather_than_the_command() -> None:
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
