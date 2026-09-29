import sys
from enum import StrEnum
from typing import Literal, Protocol

import typer
from pydantic import ValidationError

from astcheck.domain.configuration import AnalysisPolicy, PluginConfiguration
from astcheck.domain.errors import AstcheckError
from astcheck.domain.models import Text, ValueModel
from astcheck.domain.oopcheck.settings import CollaboratorGroup, OopcheckSettings


class ConfigurationFormat(StrEnum):
    JSON = "json"


class ConfigurationResponse(ValueModel):
    schema_version: Literal[1]
    configuration_yaml: Text


class InteractiveInput(Protocol):
    def is_interactive(self) -> bool: ...


class TerminalInput:
    def is_interactive(self) -> bool:
        return sys.stdin.isatty()


class SetupPrompts:
    def __init__(self, *, terminal: InteractiveInput) -> None:
        self._terminal = terminal

    def prompt(self) -> AnalysisPolicy:
        if not self._terminal.is_interactive():
            raise AstcheckError("Interactive setup requires terminal input", None)
        try:
            sources = self._values(label="Source path relative to project root")
            exclusions = self._values(label="Exclusion glob")
            collaborators: list[CollaboratorGroup] = []
            while name := typer.prompt(
                "Collaborator name (blank to finish)",
                default="",
                show_default=False,
                err=True,
            ):
                collaborators.append(
                    CollaboratorGroup(
                        name=name,
                        annotations=self._values(label="Annotation spelling"),
                    )
                )
            settings = OopcheckSettings(
                max_standalone_percent=25,
                min_callable_statements=20,
                min_shared_functions=3,
                collaborators=tuple(collaborators),
            )
            return AnalysisPolicy(
                sources=sources,
                exclusions=exclusions,
                plugins=(
                    PluginConfiguration(
                        id="oopcheck", settings=settings.model_dump(mode="json")
                    ),
                ),
            )
        except ValidationError as error:
            raise AstcheckError(f"Invalid ASTcheck setup: {error}", None) from error

    def _values(self, *, label: str) -> tuple[str, ...]:
        values: list[str] = []
        while value := typer.prompt(
            f"{label} (blank to finish)", default="", show_default=False, err=True
        ):
            values.append(value)
        return tuple(values)
