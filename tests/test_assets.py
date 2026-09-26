"""Tests for the starter resources bundled with the package."""

from collections.abc import Iterator
from importlib.resources import as_file, files
from pathlib import Path

import pytest

from checksmith.config import Config, ConfigPath


@pytest.fixture
def default_assets() -> Iterator[Path]:
    resource = files("checksmith") / "assets" / "default"
    with as_file(resource) as path:
        yield path


def test_the_starter_resources_are_available(default_assets: Path) -> None:
    assert default_assets.is_dir()
    assert (default_assets / "checksmith.yaml").is_file()


def test_the_starter_config_loads_through_the_real_loader(
    default_assets: Path,
) -> None:
    config = Config.from_path(
        config_file=default_assets / "checksmith.yaml",
        working_directory=default_assets,
    )

    assert config.checks


def test_the_starter_config_references_only_files_it_ships_with(
    default_assets: Path,
) -> None:
    config = Config.from_path(
        config_file=default_assets / "checksmith.yaml",
        working_directory=default_assets,
    )
    shipped_files = {
        path.resolve() for path in default_assets.rglob("*") if path.is_file()
    }
    referenced_files = tuple(
        argument.config_path
        for check in config.checks
        for argument in check.args
        if isinstance(argument, ConfigPath)
    )

    for path in referenced_files:
        assert path.is_file()
        assert path.resolve() in shipped_files
