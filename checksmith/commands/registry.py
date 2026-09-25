from collections.abc import Mapping

from checksmith.commands.command import Command
from checksmith.commands.import_linter import ImportLinterCommand
from checksmith.commands.pyarchgraph import PyArchGraphCommand
from checksmith.commands.pytest import PytestCommand
from checksmith.commands.ruff import RuffCommand
from checksmith.commands.semgrep import SemgrepCommand
from checksmith.commands.ty import TyCommand
from checksmith.dtos import CommandName


class CommandFactory:
    @classmethod
    def for_name(cls, *, name: CommandName) -> Command:
        """Return the one command that answers to ``name``.

        A ``match`` rather than a mapping lookup: a type checker proves this
        covers every member of :class:`CommandName`, so adding another command
        is an error reported here at check time rather than a ``KeyError`` in
        front of a user.
        """
        match name:
            case CommandName.RUFF:
                return RuffCommand()
            case CommandName.SEMGREP:
                return SemgrepCommand()
            case CommandName.IMPORT_LINTER:
                return ImportLinterCommand()
            case CommandName.TY:
                return TyCommand()
            case CommandName.PYARCHGRAPH:
                return PyArchGraphCommand()
            case CommandName.PYTEST:
                return PytestCommand()

    @classmethod
    def registry(cls) -> Mapping[CommandName, Command]:
        """Every command Checksmith ships, keyed by the config value that names it.

        Built from the enum rather than written out, so the mapping is total: a
        check's ``command`` field has already been validated against the same
        members, and so can never miss.
        """
        return {name: cls.for_name(name=name) for name in CommandName}
