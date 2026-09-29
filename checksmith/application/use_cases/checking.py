from pathlib import Path

from checksmith.application.ports.configuration import ConfigurationLoader
from checksmith.application.ports.execution import CommandRegistry, ProcessOutput
from checksmith.application.use_cases.runner import Runner
from checksmith.domain.errors import ChecksmithError
from checksmith.domain.results import CheckOutput


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
        output: ProcessOutput | None,
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
        ).check(output=output)
