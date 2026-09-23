"""Tests for :mod:`checksmith.commands.registry`."""

from collections.abc import Callable

import pytest

from checksmith.commands.command import Command
from checksmith.commands.import_linter import ImportLinterCommand
from checksmith.commands.pyarchgraph import PyArchGraphCommand
from checksmith.commands.registry import CommandFactory
from checksmith.commands.ruff import RuffCommand
from checksmith.commands.semgrep import SemgrepCommand
from checksmith.commands.ty import TyCommand
from checksmith.dtos import CommandName


def test_importing_the_registry_loads_each_command_implementation(
    imported_modules: Callable[[str], frozenset[str]],
) -> None:
    modules = imported_modules("checksmith.commands.registry")

    assert {
        "checksmith.commands.command",
        "checksmith.commands.import_linter",
        "checksmith.commands.pyarchgraph",
        "checksmith.commands.registry",
        "checksmith.commands.ruff",
        "checksmith.commands.semgrep",
        "checksmith.commands.ty",
    } <= modules


@pytest.mark.parametrize(
    ("command_name", "expected"),
    [
        (CommandName.RUFF, RuffCommand),
        (CommandName.SEMGREP, SemgrepCommand),
        (CommandName.IMPORT_LINTER, ImportLinterCommand),
        (CommandName.TY, TyCommand),
        (CommandName.PYARCHGRAPH, PyArchGraphCommand),
    ],
)
def test_the_configured_command_selects_the_class_that_handles_it(
    command_name: CommandName,
    expected: type[Command],
) -> None:
    """The ``command`` field is the whole of the dispatch; nothing else selects."""
    command = CommandFactory.for_name(name=command_name)

    assert isinstance(command, expected)
    assert command.name is command_name


def test_the_registry_covers_every_command_a_config_may_name() -> None:
    """Total by construction, so a check's ``command`` can never miss."""
    registry = CommandFactory.registry()

    assert set(registry) == set(CommandName)


def test_each_registered_command_answers_to_the_key_it_is_filed_under() -> None:
    """A command filed under the wrong key would run the wrong program."""
    registry = CommandFactory.registry()

    assert all(command.name is name for name, command in registry.items())


@pytest.mark.parametrize("command_name", list(CommandName))
def test_a_command_holds_no_configuration_of_its_own(
    command_name: CommandName,
) -> None:
    """Statelessness is the point: one instance serves every check that names it."""
    command = CommandFactory.for_name(name=command_name)

    assert vars(command) == {}
