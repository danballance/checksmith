"""Tests for the starter resources bundled with the package.

These load the shipped files through :mod:`importlib.resources` and the real
loader, so a template that could not actually validate fails here rather than in
somebody's project.
"""

import tomllib
from collections.abc import Iterator
from importlib.resources import as_file, files
from pathlib import Path

import pytest

from checksmith.config import Config
from checksmith.dtos import CommandName, PackageType


@pytest.fixture
def default_assets() -> Iterator[Path]:
    """The starter directory, located without assuming a source checkout."""
    resource = files("checksmith") / "assets" / "default"
    with as_file(resource) as path:
        yield path


def test_the_starter_directory_ships_both_files(default_assets: Path) -> None:
    """Dot-entries are skipped: Ruff plants a cache beside any config it finds."""
    shipped = sorted(
        item.name
        for item in default_assets.iterdir()
        if not item.name.startswith(".")
    )

    assert shipped == ["checksmith.yaml", "ruff.toml"]


def test_the_starter_config_loads_through_the_real_loader(
    default_assets: Path,
) -> None:
    config = Config.from_path(
        config_path=default_assets / "checksmith.yaml",
        working_directory=default_assets,
    )

    # ``project_root: ..`` resolves against the directory the config sits in.
    assert config.project_root == default_assets.parent
    assert tuple(check.id for check in config.checks) == ("ruff",)


def test_the_starter_config_references_only_files_it_ships_with(
    default_assets: Path,
) -> None:
    """Nothing resolves these arguments, so the test has to check them itself.

    Without it, a wheel whose packaged data stopped matching would ship a
    config pointing at a file that is not in the distribution.
    """
    config = Config.from_path(
        config_path=default_assets / "checksmith.yaml",
        working_directory=default_assets,
    )

    assert "./ruff.toml" in config.checks[0].args
    assert (default_assets / "ruff.toml").is_file()


def test_the_starter_config_builds_a_ruff_invocation(
    default_assets: Path,
) -> None:
    config = Config.from_path(
        config_path=default_assets / "checksmith.yaml",
        working_directory=default_assets,
    )

    assert config.checks[0].argv == (
        "uvx",
        "--from",
        "ruff==0.16.7",
        "ruff",
        "check",
        "--config",
        "./ruff.toml",
        "--output-format",
        "json",
        ".",
    )


def test_the_starter_config_asks_for_the_output_its_command_reads(
    default_assets: Path,
) -> None:
    """A config must name a command whose adapter reads what its args produce.

    Checksmith appends nothing to the vector, so the starter has to carry the
    switch itself, and this is what pairs it with :class:`RuffCommand`.
    """
    config = Config.from_path(
        config_path=default_assets / "checksmith.yaml",
        working_directory=default_assets,
    )

    assert config.checks[0].command is CommandName.RUFF
    assert "--output-format" in config.checks[0].args
    assert "json" in config.checks[0].args


def test_the_starter_check_uses_the_python_package_type(default_assets: Path) -> None:
    config = Config.from_path(
        config_path=default_assets / "checksmith.yaml",
        working_directory=default_assets,
    )

    assert config.checks[0].package_type is PackageType.UVX


def test_the_starter_ruff_configuration_is_a_standalone_one(
    default_assets: Path,
) -> None:
    """A standalone file uses ``[lint]``; ``[tool.ruff.lint]`` is pyproject's."""
    settings = tomllib.loads(
        (default_assets / "ruff.toml").read_text(encoding="utf-8")
    )

    assert "lint" in settings
    assert "tool" not in settings


def test_the_starter_ruff_configuration_selects_its_rules_explicitly(
    default_assets: Path,
) -> None:
    """An upgrade must not be able to change what this gate enforces."""
    settings = tomllib.loads(
        (default_assets / "ruff.toml").read_text(encoding="utf-8")
    )

    assert settings["lint"]["select"] != []
    assert settings["target-version"] == "py312"
