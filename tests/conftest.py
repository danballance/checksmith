"""Shared fixtures for the Checksmith test suite."""

import logging
from collections.abc import Iterator
from pathlib import Path

import pytest

from checksmith.config import Config
from checksmith.logs import LOGGER_NAME


@pytest.fixture(autouse=True)
def restore_the_package_logger() -> Iterator[None]:
    """Put the ``checksmith`` logger back as every test found it.

    ``configure_logging`` mutates process-global state, and the CLI calls it on
    every invocation. Without this, one test would decide what the next logs.
    """
    logger = logging.getLogger(LOGGER_NAME)
    handlers = list(logger.handlers)
    level = logger.level
    propagate = logger.propagate
    yield
    logger.handlers = handlers
    logger.setLevel(level)
    logger.propagate = propagate


CONFIG_TEXT = """\
schema_version: 1

project_root: ..

checks:
  - id: ruff
    runner: uvx
    package: "ruff==0.16.7"
    command: ruff

    args:
      - check
      - --config
      - ./ruff.toml
      - .
"""

RUFF_TOML_TEXT = """\
target-version = "py312"

[lint]
select = ["E", "F"]
"""


@pytest.fixture
def resolved_config(tmp_path: Path) -> Config:
    """A configuration built from a document, reading nothing off the disk.

    Built through the one construction path there is, so it cannot drift from
    what a real config file produces.
    """
    return Config.from_mapping(
        document={
            "schema_version": 1,
            "project_root": "..",
            "checks": [
                {
                    "id": "ruff",
                    "runner": "uvx",
                    "package": "ruff==0.16.7",
                    "command": "ruff",
                    "args": ["check", "--config", "./ruff.toml", "."],
                },
                {
                    "id": "prettier",
                    "runner": "npx",
                    "package": "prettier@3.6.2",
                    "command": "prettier",
                    "args": ["--check", "."],
                },
            ],
        },
        config_path=tmp_path / ".checksmith" / "checksmith.yaml",
    )


@pytest.fixture
def config_tree(tmp_path: Path) -> Path:
    """A project directory holding ``.checksmith/{checksmith.yaml,ruff.toml}``.

    Returns the directory the ``.checksmith`` directory sits in.
    """
    directory = tmp_path / ".checksmith"
    directory.mkdir()
    (directory / "checksmith.yaml").write_text(CONFIG_TEXT, encoding="utf-8")
    (directory / "ruff.toml").write_text(RUFF_TOML_TEXT, encoding="utf-8")
    return tmp_path


@pytest.fixture
def config_path(config_tree: Path) -> Path:
    """The config file inside :func:`config_tree`, which every run must name."""
    return config_tree / ".checksmith" / "checksmith.yaml"
