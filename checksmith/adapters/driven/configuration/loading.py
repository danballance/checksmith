import logging
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol

import yaml

from checksmith.domain.config import Config
from checksmith.domain.errors import ConfigSyntaxError

logger = logging.getLogger(__name__)


class ConfigSource(Protocol):
    def read(self, *, path: Path) -> Mapping[object, object]: ...


class LocalYamlConfigSource:
    def read(self, *, path: Path) -> Mapping[object, object]:
        try:
            with path.open(encoding="utf-8") as stream:
                document = yaml.safe_load(stream)
        except (OSError, UnicodeDecodeError, yaml.YAMLError) as error:
            raise ConfigSyntaxError(config_file=path, problem=str(error)) from error
        if not isinstance(document, Mapping):
            raise ConfigSyntaxError(
                config_file=path,
                problem=(
                    f'expected a mapping at the top level in "{path}", '
                    f"found {type(document).__name__}"
                ),
            )
        logger.debug(
            "read %s: top-level keys %s", path, sorted(str(key) for key in document)
        )
        return document


class ConfigLoader:
    def __init__(self, source: ConfigSource) -> None:
        self._source = source

    def load(self, *, config_file: Path, working_directory: Path) -> Config:
        path = Path(os.path.normpath(working_directory / config_file))
        logger.debug(
            "resolving %s against %s -> %s", config_file, working_directory, path
        )
        return Config.from_mapping(
            document=self._source.read(path=path), config_file=path
        )
