import logging
import os
import re
import stat
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Final, Protocol

from pydantic import BaseModel, ConfigDict, ValidationError

from checksmith.adapters.driven.execution.command import CapturedOutputCommand
from checksmith.adapters.driven.execution.prerequisites import (
    ProjectFiles,
    UvPrerequisites,
)
from checksmith.adapters.driven.execution.processes import ProcessExecutor
from checksmith.domain.config import Check
from checksmith.domain.errors import CheckOutputError, CheckPrerequisiteError
from checksmith.domain.models import CheckResult, CheckStatus, CommandName

logger = logging.getLogger(__name__)

IMPORT_LINTER_SUMMARY: Final = re.compile(
    r"Contracts: ([0-9]+) kept, ([0-9]+) broken\."
)


class ImportLinterContract(BaseModel):
    """A contract table, whose individual options Import Linter validates."""

    model_config = ConfigDict(frozen=True, strict=True, extra="ignore")


class ImportLinterConfiguration(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="ignore")

    contracts: list[ImportLinterContract]


class ImportLinterEligibility(Protocol):
    def is_runnable(self, *, check: Check, project_root: Path) -> bool: ...


class ImportLinterPrerequisites:
    def __init__(self, files: ProjectFiles) -> None:
        self._files = files

    def is_runnable(self, *, check: Check, project_root: Path) -> bool:
        config_file = project_root / "pyproject.toml"
        self._validate_arguments(check=check, project_root=project_root)
        try:
            root_mode = self._files.stat(
                path=project_root, follow_symlinks=True
            ).st_mode
            if not stat.S_ISDIR(root_mode):
                raise NotADirectoryError(
                    f"Project root is not a directory: {project_root}"
                )
            try:
                self._files.stat(path=config_file, follow_symlinks=False)
            except FileNotFoundError:
                logger.debug("%s is not runnable: %s is absent", check.id, config_file)
                return False
            document = self._files.read_toml(path=config_file)
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
            raise CheckPrerequisiteError(
                check_id=check.id,
                command=CommandName.IMPORT_LINTER,
                config_file=config_file,
                problem=str(error),
            ) from error

        section: Mapping[str, object] = document
        for key, location in (("tool", "tool"), ("importlinter", "tool.importlinter")):
            if key not in section:
                logger.debug("%s is not runnable: %s is absent", check.id, location)
                return False
            value = section[key]
            if not isinstance(value, Mapping):
                raise CheckPrerequisiteError(
                    check_id=check.id,
                    command=CommandName.IMPORT_LINTER,
                    config_file=config_file,
                    problem=f"{location} must be a TOML table",
                )
            section = value
        if "contracts" not in section:
            logger.debug(
                "%s is not runnable: tool.importlinter.contracts is absent", check.id
            )
            return False
        try:
            configuration = ImportLinterConfiguration.model_validate(section)
        except ValidationError as error:
            raise CheckPrerequisiteError(
                check_id=check.id,
                command=CommandName.IMPORT_LINTER,
                config_file=config_file,
                problem=(
                    "tool.importlinter.contracts must be a list of TOML tables:\n"
                    f"{error}"
                ),
            ) from error
        if not configuration.contracts:
            logger.debug(
                "%s is not runnable: tool.importlinter.contracts is empty", check.id
            )
            return False
        return True

    def _validate_arguments(self, *, check: Check, project_root: Path) -> None:
        config_file = project_root / "pyproject.toml"
        arguments = tuple(
            argument if isinstance(argument, str) else str(argument.config_path)
            for argument in check.args
        )
        if not arguments or arguments[0] != "lint":
            raise CheckPrerequisiteError(
                check_id=check.id,
                command=CommandName.IMPORT_LINTER,
                config_file=config_file,
                problem="Import Linter args must start with the 'lint' subcommand",
            )
        config_arguments: list[str] = []
        index = 1
        while index < len(arguments):
            argument = arguments[index]
            if argument == "--":
                break
            if argument in {"--contract", "--cache-dir"}:
                index += 2
                continue
            if argument == "--config":
                if index + 1 == len(arguments) or arguments[index + 1].startswith("-"):
                    raise CheckPrerequisiteError(
                        check_id=check.id,
                        command=CommandName.IMPORT_LINTER,
                        config_file=config_file,
                        problem="--config requires a path to the project's pyproject.toml",
                    )
                config_arguments.append(arguments[index + 1])
                index += 1
            elif argument.startswith("--config="):
                config_arguments.append(argument.removeprefix("--config="))
            index += 1
        if len(config_arguments) != 1:
            raise CheckPrerequisiteError(
                check_id=check.id,
                command=CommandName.IMPORT_LINTER,
                config_file=config_file,
                problem="Import Linter args must contain exactly one explicit --config",
            )
        configured_path = project_root / config_arguments[0]
        try:
            matching_path = os.path.abspath(configured_path) == os.path.abspath(
                config_file
            ) and self._files.resolve(path=configured_path) == self._files.resolve(
                path=config_file
            )
        except (OSError, ValueError) as error:
            raise CheckPrerequisiteError(
                check_id=check.id,
                command=CommandName.IMPORT_LINTER,
                config_file=config_file,
                problem=str(error),
            ) from error
        if not matching_path:
            raise CheckPrerequisiteError(
                check_id=check.id,
                command=CommandName.IMPORT_LINTER,
                config_file=config_file,
                problem=f"--config must target the project's {config_file}",
            )


class ImportLinterCommand(CapturedOutputCommand):
    def __init__(
        self,
        executor: ProcessExecutor,
        uv_prerequisites: UvPrerequisites,
        prerequisites: ImportLinterEligibility,
    ) -> None:
        super().__init__(executor=executor, uv_prerequisites=uv_prerequisites)
        self._prerequisites = prerequisites

    @property
    def name(self) -> CommandName:
        return CommandName.IMPORT_LINTER

    def check_is_runnable(self, *, check: Check, project_root: Path) -> bool:
        return self._prerequisites.is_runnable(check=check, project_root=project_root)

    def process_response(
        self,
        *,
        check_id: str,
        project_root: Path,
        exit_code: int,
        stdout: str,
        stderr: str,
    ) -> CheckResult:
        messages = tuple(
            output.strip() for output in (stdout, stderr) if output.strip()
        )
        problem = "\n".join(messages)
        if exit_code not in {0, 1}:
            raise CheckOutputError(
                check_id=check_id,
                command=self.name,
                summary=f"import-linter exited {exit_code} rather than reporting contracts",
                problem=problem,
            )
        summaries = tuple(
            match
            for line in stdout.splitlines()
            if (match := IMPORT_LINTER_SUMMARY.fullmatch(line)) is not None
        )
        if len(summaries) != 1:
            raise CheckOutputError(
                check_id=check_id,
                command=self.name,
                summary="import-linter did not return exactly one complete contracts summary",
                problem=problem,
            )
        broken = int(summaries[0].group(2))
        if (exit_code == 0 and broken != 0) or (exit_code == 1 and broken == 0):
            raise CheckOutputError(
                check_id=check_id,
                command=self.name,
                summary="import-linter's contracts summary contradicts its exit code",
                problem=problem,
            )
        return CheckResult(
            check_id=check_id,
            status=CheckStatus.FAILED if broken else CheckStatus.PASSED,
            messages=messages,
        )
