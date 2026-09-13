"""Tests for :mod:`checksmith.config`."""

from pathlib import Path

import pytest

from checksmith.config import Config


@pytest.fixture
def config_path() -> Path:
    """Path a ``Config`` is pointed at. No file is created there."""
    return Path("checksmith.yaml")


@pytest.fixture
def config(config_path: Path) -> Config:
    return Config(path=config_path)


def test_config_retains_the_path_it_was_given(config_path: Path) -> None:
    config = Config(path=config_path)

    assert config.path == config_path
