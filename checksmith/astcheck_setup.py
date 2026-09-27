import sys
from pathlib import Path
from typing import Protocol

import typer
import yaml
from pydantic import ValidationError

from astcheck.domain.configuration import AnalysisPolicy, PluginConfiguration
from astcheck.domain.errors import AstcheckError
from astcheck.plugins.oopcheck.settings import CollaboratorGroup, OopcheckSettings
from astcheck.ports import PluginRegistry
from checksmith.errors import ChecksmithError


class InteractiveInput(Protocol):
    def is_interactive(self) -> bool: ...


class TerminalInput:
    def is_interactive(self) -> bool:
        return sys.stdin.isatty()


class AstcheckSetup:
    def __init__(self, registry: PluginRegistry, terminal: InteractiveInput) -> None:
        self._registry = registry
        self._terminal = terminal

    def require_interactive(self, *, missing_options: tuple[str, ...]) -> None:
        if missing_options and not self._terminal.is_interactive():
            raise ChecksmithError(
                "Noninteractive initialization requires "
                + " and ".join(missing_options)
                + "."
            )

    def policy(self, *, config_file: Path | None) -> AnalysisPolicy:
        policy = (
            self._read_policy(config_file=config_file)
            if config_file is not None
            else self._prompt_policy()
        )
        try:
            for plugin in policy.plugins:
                self._registry.load(plugin_id=plugin.id).create(
                    settings=plugin.settings
                )
        except (AstcheckError, ValueError) as error:
            raise ChecksmithError(
                f"Invalid ASTcheck plugin configuration: {error}"
            ) from error
        return policy

    def _read_policy(self, *, config_file: Path) -> AnalysisPolicy:
        try:
            document: object = yaml.safe_load(config_file.read_text(encoding="utf-8"))
            return AnalysisPolicy.model_validate(document)
        except (OSError, UnicodeDecodeError, yaml.YAMLError) as error:
            raise ChecksmithError(
                f"Could not read ASTcheck policy {config_file}: {error}"
            ) from error
        except (TypeError, ValueError) as error:
            raise ChecksmithError(
                f"Invalid ASTcheck policy {config_file}: {error}"
            ) from error

    def _prompt_policy(self) -> AnalysisPolicy:
        try:
            sources = self._prompt_values(label="Source path relative to project root")
            exclusions = self._prompt_values(label="Exclusion glob")
            collaborators: list[CollaboratorGroup] = []
            while name := typer.prompt(
                "Collaborator name (blank to finish)", default="", show_default=False
            ):
                collaborators.append(
                    CollaboratorGroup(
                        name=name,
                        annotations=self._prompt_values(label="Annotation spelling"),
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
            raise ChecksmithError(f"Invalid ASTcheck setup: {error}") from error

    def _prompt_values(self, *, label: str) -> tuple[str, ...]:
        values: list[str] = []
        while value := typer.prompt(
            f"{label} (blank to finish)", default="", show_default=False
        ):
            values.append(value)
        return tuple(values)
