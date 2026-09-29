import json
from pathlib import Path

from pydantic import (
    BaseModel,
    ConfigDict,
    JsonValue,
    StrictInt,
    StrictStr,
    ValidationError,
    field_validator,
)

from checksmith.adapters.driven.astcheck_setup.process import SetupProcess
from checksmith.application.ports.initialization import SetupCommandBuilder
from checksmith.domain.errors import ChecksmithError


class ConfigureResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: StrictInt
    configuration_yaml: StrictStr

    @field_validator("schema_version")
    @classmethod
    def _supported_version(cls, value: int) -> int:
        if value != 1:
            raise ValueError(f"unsupported configure response schema version: {value}")
        return value

    @field_validator("configuration_yaml")
    @classmethod
    def _nonempty_configuration(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("configuration_yaml must not be blank")
        return value


def _unique_object(pairs: list[tuple[str, JsonValue]]) -> dict[str, JsonValue]:
    result: dict[str, JsonValue] = {}
    for name, value in pairs:
        if name in result:
            raise ValueError(f"duplicate JSON field: {name}")
        result[name] = value
    return result


class AstcheckSetup:
    def __init__(self, process: SetupProcess, commands: SetupCommandBuilder) -> None:
        self._process = process
        self._commands = commands

    def configure(
        self,
        *,
        package: str,
        project_root: Path,
        config_file: Path,
        policy: Path | None,
    ) -> bytes:
        for path in (project_root, config_file, *((policy,) if policy else ())):
            if not path.is_absolute():
                raise ChecksmithError(f"ASTcheck setup requires absolute paths: {path}")
        mode = ("--policy", str(policy)) if policy is not None else ("--interactive",)
        argv = self._commands.build_argv(
            package=package,
            command="astcheck",
            arguments=(
                "configure",
                "--project-root",
                str(project_root),
                "--config-file",
                str(config_file),
                *mode,
                "--format",
                "json",
            ),
        )
        try:
            result = self._process.run(
                argv=argv, cwd=project_root, interactive=policy is None
            )
        except OSError as error:
            raise ChecksmithError(
                f"Could not run ASTcheck configure: {error}"
            ) from error
        except KeyboardInterrupt as error:
            raise ChecksmithError("ASTcheck setup cancelled.") from error
        if result.returncode != 0:
            raise ChecksmithError(
                f"ASTcheck configure failed with exit code {result.returncode}."
            )
        try:
            document: object = json.loads(
                result.stdout.decode("utf-8"), object_pairs_hook=_unique_object
            )
            response = ConfigureResponse.model_validate(document)
            return response.configuration_yaml.encode("utf-8")
        except (ValueError, UnicodeError, ValidationError) as error:
            raise ChecksmithError(
                f"Invalid ASTcheck configure response: {error}"
            ) from error
