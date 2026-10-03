"""Tests for :mod:`checksmith.adapters.driven.execution.registry`."""

from collections.abc import Callable
from pathlib import Path

import pytest

from checksmith.adapters.driven.execution.astcheck import AstcheckCommand
from checksmith.adapters.driven.execution.command import CapturedOutputCommand, Command
from checksmith.adapters.driven.execution.complexipy import ComplexipyCommand
from checksmith.adapters.driven.execution.import_linter import ImportLinterCommand
from checksmith.adapters.driven.execution.pyarchgraph import PyArchGraphCommand
from checksmith.adapters.driven.execution.pytest import PytestCommand
from checksmith.adapters.driven.execution.registry import CommandFactory
from checksmith.adapters.driven.execution.ruff import RuffCommand
from checksmith.adapters.driven.execution.semgrep import SemgrepCommand
from checksmith.adapters.driven.execution.ty import TyCommand
from checksmith.adapters.driven.execution.vulture import VultureCommand
from checksmith.domain.models import CheckStatus, CommandName
from tests.checksmith.adapters.driven.execution.test_astcheck import CLEAN_REPORT
from tests.checksmith.adapters.driven.execution.test_pyarchgraph import HEALTHY_REPORT


def test_importing_the_registry_loads_each_command_implementation(
    imported_modules: Callable[[str], frozenset[str]],
) -> None:
    modules = imported_modules("checksmith.adapters.driven.execution.registry")

    assert {
        "checksmith.adapters.driven.execution.astcheck",
        "checksmith.adapters.driven.execution.command",
        "checksmith.adapters.driven.execution.complexipy",
        "checksmith.adapters.driven.execution.import_linter",
        "checksmith.adapters.driven.execution.pyarchgraph",
        "checksmith.adapters.driven.execution.pytest",
        "checksmith.adapters.driven.execution.registry",
        "checksmith.adapters.driven.execution.ruff",
        "checksmith.adapters.driven.execution.semgrep",
        "checksmith.adapters.driven.execution.ty",
        "checksmith.adapters.driven.execution.vulture",
    } <= modules


@pytest.mark.parametrize(
    ("command_name", "expected"),
    [
        (CommandName.ASTCHECK, AstcheckCommand),
        (CommandName.COMPLEXIPY, ComplexipyCommand),
        (CommandName.RUFF, RuffCommand),
        (CommandName.SEMGREP, SemgrepCommand),
        (CommandName.IMPORT_LINTER, ImportLinterCommand),
        (CommandName.TY, TyCommand),
        (CommandName.PYARCHGRAPH, PyArchGraphCommand),
        (CommandName.PYTEST, PytestCommand),
        (CommandName.VULTURE, VultureCommand),
    ],
)
def test_the_configured_command_selects_the_class_that_handles_it(
    command_name: CommandName,
    expected: type[Command],
    command_factory: CommandFactory,
) -> None:
    """The ``command`` field is the whole of the dispatch; nothing else selects."""
    command = command_factory.for_name(name=command_name)

    assert isinstance(command, expected)
    assert command.name is command_name


def test_the_registry_covers_every_command_a_config_may_name(
    command_factory: CommandFactory,
) -> None:
    """Total by construction, so a check's ``command`` can never miss."""
    registry = command_factory.registry()

    assert set(registry) == set(CommandName)


def test_each_registered_command_answers_to_the_key_it_is_filed_under(
    command_factory: CommandFactory,
) -> None:
    """A command filed under the wrong key would run the wrong program."""
    registry = command_factory.registry()

    assert all(command.name is name for name, command in registry.items())


@pytest.mark.parametrize(
    ("command_name", "exit_code", "stdout"),
    [
        (CommandName.ASTCHECK, 0, CLEAN_REPORT),
        (CommandName.COMPLEXIPY, 0, ""),
        (CommandName.RUFF, 0, "[]"),
        (CommandName.SEMGREP, 0, '{"results": [], "errors": []}'),
        (CommandName.IMPORT_LINTER, 0, "Contracts: 1 kept, 0 broken."),
        (CommandName.TY, 0, "[]"),
        (
            CommandName.PYARCHGRAPH,
            0,
            HEALTHY_REPORT,
        ),
        (CommandName.VULTURE, 0, ""),
    ],
)
def test_a_command_can_process_different_checks_without_retaining_check_state(
    command_name: CommandName,
    exit_code: int,
    stdout: str,
    command_factory: CommandFactory,
) -> None:
    command = command_factory.for_name(name=command_name)
    assert isinstance(command, CapturedOutputCommand)
    initial_state = vars(command).copy()

    first = command.process_response(
        check_id="first",
        project_root=Path("/workspace/first"),
        exit_code=exit_code,
        stdout=stdout,
        stderr="",
    )
    second = command.process_response(
        check_id="second",
        project_root=Path("/workspace/second"),
        exit_code=exit_code,
        stdout=stdout,
        stderr="",
    )

    assert first.check_id == "first"
    assert second.check_id == "second"
    assert first.status is CheckStatus.PASSED
    assert second.status is CheckStatus.PASSED
    assert vars(command) == initial_state
