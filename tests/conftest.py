"""Shared fixtures for the Checksmith test suite."""

from pathlib import Path

import pytest

from checksmith.config import Config


@pytest.fixture
def config() -> Config:
    return Config(path=Path("tools.yaml"))
