from collections.abc import Mapping
from pathlib import Path
from typing import Protocol

from checksmith.commands.command import Command
from checksmith.config import Config
from checksmith.dtos import CommandName
from checksmith.errors import ChecksmithError
from checksmith.outputs.checkoutput import CheckOutput
from checksmith.runner import Runner


class ConfigurationLoader(Protocol):
    def load(self, *, config_file: Path, working_directory: Path) -> Config: ...


class CommandRegistry(Protocol):
    def registry(self) -> Mapping[CommandName, Command]: ...


class CheckService:
    def __init__(self, loader: ConfigurationLoader, commands: CommandRegistry) -> None:
        self._loader = loader
        self._commands = commands

    def check(
        self,
        *,
        config_file: Path,
        working_directory: Path,
        check_id: str | None,
    ) -> CheckOutput:
        config = self._loader.load(
            config_file=config_file, working_directory=working_directory
        )
        checks = config.checks
        if check_id is not None:
            checks = tuple(check for check in checks if check.id == check_id)
            if not checks:
                available = ", ".join(repr(check.id) for check in config.checks)
                raise ChecksmithError(
                    f"Unknown check ID {check_id!r}. Available check IDs: {available}."
                )
        return Runner(
            checks=checks,
            commands=self._commands.registry(),
            project_root=config.project_root,
        ).check()
