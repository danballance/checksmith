from collections.abc import Mapping

from checksmith.commands.astcheck import AstcheckCommand
from checksmith.commands.command import Command
from checksmith.commands.complexipy import ComplexipyCommand
from checksmith.commands.import_linter import (
    ImportLinterCommand,
    ImportLinterEligibility,
)
from checksmith.commands.pyarchgraph import PyArchGraphCommand
from checksmith.commands.pytest import LauncherSource, PytestCommand
from checksmith.commands.ruff import RuffCommand
from checksmith.commands.semgrep import SemgrepCommand
from checksmith.commands.ty import TyCommand
from checksmith.dtos import CommandName
from checksmith.packages import Package
from checksmith.prerequisites import UvPrerequisites
from checksmith.processes import ProcessExecutor


class CommandFactory:
    def __init__(
        self,
        executor: ProcessExecutor,
        uv_prerequisites: UvPrerequisites,
        import_linter_prerequisites: ImportLinterEligibility,
        launcher_source: LauncherSource,
        pytest_package: Package,
    ) -> None:
        self._executor = executor
        self._uv_prerequisites = uv_prerequisites
        self._import_linter_prerequisites = import_linter_prerequisites
        self._launcher_source = launcher_source
        self._pytest_package = pytest_package

    def for_name(self, *, name: CommandName) -> Command:
        """Return the one command that answers to ``name``.

        A ``match`` rather than a mapping lookup: a type checker proves this
        covers every member of :class:`CommandName`, so adding another command
        is an error reported here at check time rather than a ``KeyError`` in
        front of a user.
        """
        match name:
            case CommandName.COMPLEXIPY:
                return ComplexipyCommand(
                    executor=self._executor, uv_prerequisites=self._uv_prerequisites
                )
            case CommandName.ASTCHECK:
                return AstcheckCommand(
                    executor=self._executor, uv_prerequisites=self._uv_prerequisites
                )
            case CommandName.RUFF:
                return RuffCommand(
                    executor=self._executor, uv_prerequisites=self._uv_prerequisites
                )
            case CommandName.SEMGREP:
                return SemgrepCommand(
                    executor=self._executor, uv_prerequisites=self._uv_prerequisites
                )
            case CommandName.IMPORT_LINTER:
                return ImportLinterCommand(
                    executor=self._executor,
                    uv_prerequisites=self._uv_prerequisites,
                    prerequisites=self._import_linter_prerequisites,
                )
            case CommandName.TY:
                return TyCommand(
                    executor=self._executor, uv_prerequisites=self._uv_prerequisites
                )
            case CommandName.PYARCHGRAPH:
                return PyArchGraphCommand(
                    executor=self._executor, uv_prerequisites=self._uv_prerequisites
                )
            case CommandName.PYTEST:
                return PytestCommand(
                    executor=self._executor,
                    uv_prerequisites=self._uv_prerequisites,
                    source=self._launcher_source,
                    package=self._pytest_package,
                )

    def registry(self) -> Mapping[CommandName, Command]:
        """Every command Checksmith ships, keyed by the config value that names it.

        Built from the enum rather than written out, so the mapping is total: a
        check's ``command`` field has already been validated against the same
        members, and so can never miss.
        """
        return {name: self.for_name(name=name) for name in CommandName}
