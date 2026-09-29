from importlib.resources import files
from pathlib import Path

import pytest
from pydantic import BaseModel

from checksmith.adapters.driven.configuration.initialization import YamlConfigRenderer
from checksmith.adapters.driven.filesystem.initialization import (
    LocalInitializationFilesystem,
    PackagedAssetSource,
)
from checksmith.application.use_cases.initialization import Initializer
from checksmith.domain.errors import ChecksmithError

ASSET_NAMES = Initializer.ASSET_NAMES


class ConfigureCall(BaseModel):
    package: str
    project_root: Path
    config_file: Path
    policy: Path | None


class RecordingConfigurator:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.calls: list[ConfigureCall] = []
        self.configuration = b"generated ASTcheck YAML\n"
        self.error: ChecksmithError | None = None

    def configure(
        self,
        *,
        package: str,
        project_root: Path,
        config_file: Path,
        policy: Path | None,
    ) -> bytes:
        self.events.append("configure:astcheck.yaml")
        self.calls.append(
            ConfigureCall(
                package=package,
                project_root=project_root,
                config_file=config_file,
                policy=policy,
            )
        )
        if self.error is not None:
            raise self.error
        return self.configuration


@pytest.fixture
def configurator(events: list[str]) -> RecordingConfigurator:
    return RecordingConfigurator(events=events)


@pytest.fixture
def events() -> list[str]:
    return []


@pytest.fixture
def destination(tmp_path: Path) -> Path:
    directory = tmp_path / "configuration"
    directory.mkdir()
    return directory


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    directory = tmp_path / "project"
    directory.mkdir()
    return directory


@pytest.fixture
def template() -> bytes:
    return (files("checksmith") / "assets" / "default" / "checksmith.yaml").read_bytes()


@pytest.fixture
def asset_directory(tmp_path: Path) -> Path:
    directory = tmp_path / "assets"
    directory.mkdir()
    packaged = files("checksmith") / "assets" / "default"
    for name in ASSET_NAMES:
        (directory / name).write_bytes((packaged / name).read_bytes())
    return directory


@pytest.fixture
def initializer(
    asset_directory: Path, configurator: RecordingConfigurator
) -> Initializer:
    return Initializer(
        assets=PackagedAssetSource(directory=asset_directory),
        renderer=YamlConfigRenderer(),
        filesystem=LocalInitializationFilesystem(),
        astcheck=configurator,
    )
