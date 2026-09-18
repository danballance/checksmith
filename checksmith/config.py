"""Reading, validating and resolving the Checksmith config file."""

import logging
import os
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

from checksmith.dtos import CommandName, PackageType
from checksmith.errors import (
    ConfigSchemaError,
    ConfigSyntaxError,
    SchemaViolation,
)
from checksmith.packages import Package

logger = logging.getLogger(__name__)

SUPPORTED_SCHEMA_VERSION: Final = 1


class Check(BaseModel):
    """A single check: one configuration of one command.
    Two checks may name the same command and differ only in their arguments.
    """

    id: str = Field(min_length=1)
    package_type: PackageType
    package: str = Field(min_length=1)
    command: CommandName
    args: tuple[str, ...]

    @field_validator("package")
    @classmethod
    def _package_names_a_version(cls, value: str, info: ValidationInfo) -> str:
        # A field validator, not a model validator, so the complaint lands on
        # ``package`` --- the line a user has to edit.
        package_type = info.data.get("package_type")
        if package_type is None:
            # ``package_type`` did not validate; that is the error worth fixing.
            return value
        package_type = cast(PackageType, package_type)
        Package.from_name(name=package_type).validate_package(package=value)
        return value

    @property
    def argv(self) -> tuple[str, ...]:
        """The vector this check runs, with ``uvx`` or ``npx`` at position zero.

        An argument *vector*, never a command string: nothing here is ever
        handed to a shell, so a path containing a space or a quote needs no
        escaping and gets none.
        """
        return Package.from_name(name=self.package_type).build_argv(
            package=self.package,
            command=self.command.value,
            arguments=self.args,
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
        config_path = info.context
        if not isinstance(config_path, Path):
            # Not a config error, so not a ``ValueError``: a complaint against
            # ``project_root`` would send a user to edit a line that is fine.
            raise TypeError(
                "Config must be validated with the config path as context; "
                "build one with Config.from_mapping"
            )
        return Path(os.path.normpath(config_path.parent / value))

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
                "check %s: package_type=%s package=%s command=%s args=%s",
                check.id,
                check.package_type,
                check.package,
                check.command,
                check.args,
            )
        return config
