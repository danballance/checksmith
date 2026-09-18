"""Reading, validating and resolving the Checksmith config file."""

import logging
import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Final, Self, cast

import yaml
from pydantic import (
    BaseModel,
    Field,
    StrictInt,
    ValidationError,
    ValidationInfo,
    field_validator,
)

from checksmith.dtos import RunnerName
from checksmith.errors import (
    ConfigSchemaError,
    ConfigSyntaxError,
    SchemaViolation,
)
from checksmith.runners import PackageRunner

logger = logging.getLogger(__name__)

SUPPORTED_SCHEMA_VERSION: Final = 1

TOKEN: Final = re.compile(r"\{files\.(?P<name>[A-Za-z_][A-Za-z0-9_-]*)\}")


class Check(BaseModel):
    """A single check, with its paths absolute and its file references substituted."""

    id: str = Field(min_length=1)
    runner: RunnerName
    package: str = Field(min_length=1)
    command: str = Field(min_length=1)
    args: tuple[str, ...]

    @field_validator("package")
    @classmethod
    def _package_names_a_version(cls, value: str, info: ValidationInfo) -> str:
        # A field validator, not a model validator, so the complaint lands on
        # ``package`` --- the line a user has to edit.
        runner = info.data.get("runner")
        if runner is None:
            # ``runner`` itself did not validate; that is the error worth fixing.
            return value
        runner = cast(RunnerName, runner)
        PackageRunner.from_name(name=runner).validate_package(package=value)
        return value


class Config(BaseModel):
    schema_version: StrictInt
    project_root: Path = Field(default=".", validate_default=True)
    checks: tuple[Check, ...]

    @field_validator("schema_version")
    @classmethod
    def _version_is_supported(cls, value: int) -> int:
        if value != SUPPORTED_SCHEMA_VERSION:
            raise ValueError(
                f"schema_version must be {SUPPORTED_SCHEMA_VERSION}, got {value}"
            )
        return value

    @field_validator("checks")
    @classmethod
    def _at_least_one_check(cls, value: tuple[Check, ...]) -> tuple[Check, ...]:
        if len(value) == 0:
            raise ValueError("a config file must declare at least one check")
        return value

    @staticmethod
    def _read(*, path: Path) -> Mapping[object, object]:
        """Read one config file as plain data, or fail with what went wrong.

        PyYAML is handed the stream and not its text, so the positions in its own
        messages name this file rather than ``<unicode string>``.
        """
        try:
            with path.open(encoding="utf-8") as stream:
                document = yaml.safe_load(stream)
        except (OSError, UnicodeDecodeError, yaml.YAMLError) as error:
            raise ConfigSyntaxError(config_path=path, problem=str(error)) from error
        if not isinstance(document, Mapping):
            # An empty file parses to ``None``, which lands here too.
            raise ConfigSyntaxError(
                config_path=path,
                problem=(
                    f'expected a mapping at the top level in "{path}", '
                    f"found {type(document).__name__}"
                ),
            )
        logger.debug(
            "read %s: top-level keys %s",
            path,
            sorted(str(key) for key in document),
        )
        return document

    @classmethod
    def from_path(cls, *, config_path: Path, working_directory: Path) -> Self:
        """Turn a ``--config`` argument into a configuration, or fail saying why.

        Two base directories are in play and are not interchangeable: the
        caller's, which ``--config`` resolves against, and the config file's
        own, which everything inside the file resolves against.
        """
        base_directory = working_directory
        value = str(config_path)
        path = Path(os.path.normpath(base_directory / value))
        logger.debug("resolving %s against %s -> %s", value, base_directory, path)
        return cls.from_mapping(document=cls._read(path=path), config_path=path)

    @classmethod
    def from_mapping(
        cls,
        *,
        document: Mapping[object, object],
        config_path: Path,
    ) -> Self:
        logger.debug(
            "validating %s against schema version %d",
            config_path,
            SUPPORTED_SCHEMA_VERSION,
        )
        try:
            config = cls.model_validate(document, context=config_path)
        except ValidationError as error:
            logger.debug(
                "%s rejected: %d schema violations",
                config_path,
                error.error_count(),
            )
            raise ConfigSchemaError(
                config_path=config_path,
                violations=tuple(
                    SchemaViolation(
                        field=".".join(str(part) for part in detail["loc"]),
                        message=detail["msg"],
                    )
                    for detail in error.errors()
                ),
            ) from error
        logger.debug(
            "parsed %d checks, project_root=%s",
            len(config.checks),
            config.project_root,
        )
        for check in config.checks:
            # Per check rather than one dump of the model: this is the level a
            # surprising run is diagnosed at --- which package, which argv.
            logger.debug(
                "check %s: runner=%s package=%s command=%s args=%s",
                check.id,
                check.runner,
                check.package,
                check.command,
                check.args,
            )
        return config
