from collections.abc import Mapping
from typing import Literal, overload

from checksmith.adapters.driven.execution.astcheck import AstcheckCommand
from checksmith.adapters.driven.execution.command import CapturedOutputCommand, Command
from checksmith.adapters.driven.execution.complexipy import ComplexipyCommand
from checksmith.adapters.driven.execution.import_linter import (
    ImportLinterCommand,
    ImportLinterEligibility,
)
from checksmith.adapters.driven.execution.packages import PackageInvocation
from checksmith.adapters.driven.execution.prerequisites import (
    ProjectFiles,
    UvPrerequisites,
)
from checksmith.adapters.driven.execution.processes import ProcessExecutor
from checksmith.adapters.driven.execution.pyarchgraph import PyArchGraphCommand
from checksmith.adapters.driven.execution.pytest import LauncherSource, PytestCommand
from checksmith.adapters.driven.execution.ruff import RuffCommand
from checksmith.adapters.driven.execution.semgrep import SemgrepCommand
from checksmith.adapters.driven.execution.ty import TyCommand
from checksmith.adapters.driven.execution.vulture import VultureCommand
from checksmith.domain.models import CommandName


class CommandFactory:
    def __init__(
        self,
        executor: ProcessExecutor,
        uv_prerequisites: UvPrerequisites,
        import_linter_prerequisites: ImportLinterEligibility,
        launcher_source: LauncherSource,
        pytest_package: PackageInvocation,
        project_files: ProjectFiles,
    ) -> None:
        self._executor = executor
        self._uv_prerequisites = uv_prerequisites
        self._import_linter_prerequisites = import_linter_prerequisites
        self._launcher_source = launcher_source
        self._pytest_package = pytest_package
        self._project_files = project_files

    @overload
    def for_name(self, *, name: Literal[CommandName.PYTEST]) -> PytestCommand: ...

    @overload
    def for_name(
        self,
        *,
        name: Literal[
            CommandName.ASTCHECK,
            CommandName.COMPLEXIPY,
            CommandName.RUFF,
            CommandName.SEMGREP,
            CommandName.IMPORT_LINTER,
            CommandName.TY,
            CommandName.PYARCHGRAPH,
            CommandName.VULTURE,
        ],
    ) -> CapturedOutputCommand: ...

    @overload
    def for_name(self, *, name: CommandName) -> Command: ...

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
            case CommandName.VULTURE:
                return VultureCommand(
                    executor=self._executor,
                    uv_prerequisites=self._uv_prerequisites,
                    files=self._project_files,
                )

    def registry(self) -> Mapping[CommandName, Command]:
        """Every command Checksmith ships, keyed by the config value that names it.

        Built from the enum rather than written out, so the mapping is total: a
        check's ``command`` field has already been validated against the same
        members, and so can never miss.
        """
        return {name: self.for_name(name=name) for name in CommandName}
