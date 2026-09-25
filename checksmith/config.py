"""Reading, validating and resolving the Checksmith config file."""

import logging
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Final, Self, cast

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    ValidationError,
    ValidationInfo,
    field_validator,
)

from checksmith.dtos import CommandName, PackageType
from checksmith.errors import (
    ConfigSchemaError,
    ConfigSyntaxError,
    SchemaViolation,
)
from checksmith.packages import Package

logger = logging.getLogger(__name__)

SUPPORTED_SCHEMA_VERSION: Final = 1


class ConfigPath(BaseModel):
    """Argument naming a file relative to the checksmith config file."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    config_path: Path

    @field_validator("config_path")
    @classmethod
    def _resolves_against_the_config_directory(
        cls,
        value: Path,
        info: ValidationInfo,
    ) -> Path:
        """Resolve the path against the directory holding the config file.
        Absolute, because the result is handed to a process whose working
        directory is the project root.
        """
        config_file = info.context
        if not isinstance(config_file, Path):
            # Not a config error, so not a ``ValueError``: a complaint against
            # ``config_path`` would send a user to edit a line that is fine.
            raise TypeError(
                "Config must be validated with the config path as context; "
                "build one with Config.from_mapping"
            )
        return Path(os.path.normpath(config_file.parent / value))


type Argument = str | ConfigPath
"""One entry in a check's argument list.

A bare string reaches the tool verbatim, so a target such as ``.`` still means
what the tool reads it as: a path relative to the project root it runs in.
"""


class Check(BaseModel):
    """A single check: one configuration of one command.
    Two checks may name the same command and differ only in their arguments.
    """

    id: str = Field(min_length=1)
    package_type: PackageType
    package: str | None = Field(min_length=1)
    command: CommandName
    args: tuple[Argument, ...]

    @field_validator("package")
    @classmethod
    def _package_matches_its_type(
        cls,
        value: str | None,
        info: ValidationInfo,
    ) -> str | None:
        # A field validator, not a model validator, so the complaint lands on
        # ``package`` --- the line a user has to edit.
        package_type = info.data.get("package_type")
        if package_type is None:
            # ``package_type`` did not validate; that is the error worth fixing.
            return value
        package_type = cast(PackageType, package_type)
        Package.from_name(name=package_type).validate_package(package=value)
        return value

    @field_validator("command")
    @classmethod
    def _pytest_uses_the_project_environment(
        cls,
        value: CommandName,
        info: ValidationInfo,
    ) -> CommandName:
        package_type = info.data.get("package_type")
        if (
            value is CommandName.PYTEST
            and package_type is not None
            and package_type is not PackageType.UV
        ):
            raise ValueError("pytest requires package_type: uv")
        return value

    @property
    def arguments(self) -> tuple[str, ...]:
        """Arguments with config-relative paths resolved into strings."""
        return tuple(
            argument if isinstance(argument, str) else str(argument.config_path)
            for argument in self.args
        )

    @property
    def argv(self) -> tuple[str, ...]:
        """The vector this check runs, beginning with its package runner.

        An argument *vector*, never a command string: nothing here is ever
        handed to a shell, so a path containing a space or a quote needs no
        escaping and gets none.

        A :class:`ConfigPath` was resolved when it was validated, so flattening
        it here is only stringification: by this point every argument is
        something the tool can be handed as it stands.
        """
        return Package.from_name(name=self.package_type).build_argv(
            package=self.package,
            command=self.command.value,
            arguments=self.arguments,
        )


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

    @field_validator("project_root")
    @classmethod
    def _resolves_against_the_config_directory(
        cls,
        value: Path,
        info: ValidationInfo,
    ) -> Path:
        """Resolve the project root against the directory holding the config file.

        Everything inside a config file is relative to that file, so a root of
        ``..`` names the directory above ``.checksmith``, not the directory
        above wherever the user happened to be located.
        """
        config_file = info.context
        if not isinstance(config_file, Path):
            # Not a config error, so not a ``ValueError``: a complaint against
            # ``project_root`` would send a user to edit a line that is fine.
            raise TypeError(
                "Config must be validated with the config path as context; "
                "build one with Config.from_mapping"
            )
        return Path(os.path.normpath(config_file.parent / value))

    @field_validator("checks")
    @classmethod
    def _at_least_one_check(cls, value: tuple[Check, ...]) -> tuple[Check, ...]:
        if len(value) == 0:
            raise ValueError("a config file must declare at least one check")
        return value

    @field_validator("checks")
    @classmethod
    def _unique_check_ids(cls, value: tuple[Check, ...]) -> tuple[Check, ...]:
        seen: set[str] = set()
        for check in value:
            if check.id in seen:
                raise ValueError(f"duplicate check ID {check.id!r}; IDs must be unique")
            seen.add(check.id)
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
            raise ConfigSyntaxError(config_file=path, problem=str(error)) from error
        if not isinstance(document, Mapping):
            # An empty file parses to ``None``, which lands here too.
            raise ConfigSyntaxError(
                config_file=path,
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
    def from_path(cls, *, config_file: Path, working_directory: Path) -> Self:
        """Turn a ``--config`` argument into a configuration, or fail saying why.

        Two base directories are in play and are not interchangeable: the
        caller's, which ``--config`` resolves against, and the config file's
        own, which everything inside the file resolves against.
        """
        base_directory = working_directory
        value = str(config_file)
        path = Path(os.path.normpath(base_directory / value))
        logger.debug("resolving %s against %s -> %s", value, base_directory, path)
        return cls.from_mapping(document=cls._read(path=path), config_file=path)

    @classmethod
    def from_mapping(
        cls,
        *,
        document: Mapping[object, object],
        config_file: Path,
    ) -> Self:
        logger.debug(
            "validating %s against schema version %d",
            config_file,
            SUPPORTED_SCHEMA_VERSION,
        )
        try:
            config = cls.model_validate(document, context=config_file)
        except ValidationError as error:
            logger.debug(
                "%s rejected: %d schema violations",
                config_file,
                error.error_count(),
            )
            raise ConfigSchemaError(
                config_file=config_file,
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
                "check %s: package_type=%s package=%s command=%s args=%s",
                check.id,
                check.package_type,
                check.package,
                check.command,
                check.args,
            )
        return config
