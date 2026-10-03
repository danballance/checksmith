"""Tests for the starter resources bundled with the package."""

import tomllib
from collections.abc import Iterator
from importlib.resources import as_file, files
from pathlib import Path

import pytest

from checksmith.adapters.driven.configuration.loading import (
    ConfigLoader,
    LocalYamlConfigSource,
)
from checksmith.adapters.driven.execution.packages import PackageInvocation
from checksmith.domain.config import ConfigPath
from checksmith.domain.models import CommandName, PackageType


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
    config = ConfigLoader(source=LocalYamlConfigSource()).load(
        config_file=default_assets / "checksmith.yaml",
        working_directory=default_assets,
    )

    assert config.checks
    architecture = tuple(
        check for check in config.checks if check.command is CommandName.PYARCHGRAPH
    )
    assert tuple(check.id for check in architecture) == (
        "pyarchgraph-modules",
        "pyarchgraph-packages",
    )
    assert all(check.package_type is PackageType.UVX for check in architecture)
    assert tuple(
        PackageInvocation.from_name(check.package_type).build_argv(
            package=check.package,
            command=check.command.value,
            arguments=check.arguments,
        )
        for check in architecture
    ) == tuple(
        (
            "uvx",
            "--isolated",
            "--refresh-package",
            "pyarchgraph",
            "--from",
            "pyarchgraph @ git+https://github.com/danballance/pyarchgraph@main",
            "pyarchgraph",
            ".",
            "--gate",
            gate,
        )
        for gate in ("structural", "package-structural")
    )


def test_the_starter_config_runs_astcheck_from_the_checksmith_git_package(
    default_assets: Path,
) -> None:
    config = ConfigLoader(source=LocalYamlConfigSource()).load(
        config_file=default_assets / "checksmith.yaml",
        working_directory=default_assets,
    )
    astcheck = next(
        check for check in config.checks if check.command is CommandName.ASTCHECK
    )

    assert astcheck.package_type is PackageType.UVX
    assert astcheck.package == (
        "checksmith @ git+https://github.com/danballance/checksmith@main"
    )
    assert PackageInvocation.from_name(astcheck.package_type).build_argv(
        package=astcheck.package,
        command=astcheck.command.value,
        arguments=astcheck.arguments,
    ) == (
        "uvx",
        "--isolated",
        "--refresh-package",
        "checksmith",
        "--from",
        "checksmith @ git+https://github.com/danballance/checksmith@main",
        "astcheck",
        "check",
        "--config",
        str(default_assets / "astcheck.yaml"),
        "--format",
        "json",
    )


def test_the_starter_config_enforces_cognitive_complexity(
    default_assets: Path,
) -> None:
    config = ConfigLoader(source=LocalYamlConfigSource()).load(
        config_file=default_assets / "checksmith.yaml",
        working_directory=default_assets,
    )
    complexity = next(
        check for check in config.checks if check.command is CommandName.COMPLEXIPY
    )

    assert complexity.id == "complexipy"
    assert complexity.package_type is PackageType.UVX
    assert PackageInvocation.from_name(complexity.package_type).build_argv(
        package=complexity.package,
        command=complexity.command.value,
        arguments=complexity.arguments,
    ) == (
        "uvx",
        "--from",
        "complexipy==8.0.1",
        "complexipy",
        "--plain",
        "--failed",
        "--max-complexity-allowed",
        "10",
        "--exclude",
        "tests/**",
        "--color",
        "no",
        "--snapshot-ignore",
        "--snapshot-create=false",
        "--ignore-complexity=false",
        "--report-ignored=false",
        ".",
    )


def test_the_starter_config_finds_dead_code_using_the_bundled_vulture_config(
    default_assets: Path,
) -> None:
    config = ConfigLoader(source=LocalYamlConfigSource()).load(
        config_file=default_assets / "checksmith.yaml",
        working_directory=default_assets,
    )
    dead_code = next(
        check for check in config.checks if check.command is CommandName.VULTURE
    )

    assert dead_code.id == "vulture"
    assert dead_code.package_type is PackageType.UVX
    assert PackageInvocation.from_name(dead_code.package_type).build_argv(
        package=dead_code.package,
        command=dead_code.command.value,
        arguments=dead_code.arguments,
    ) == (
        "uvx",
        "--from",
        "vulture==2.16",
        "vulture",
        "--config",
        str(default_assets / "vulture.toml"),
        ".",
    )


def test_the_bundled_vulture_config_keeps_the_report_vulture_checks_read(
    default_assets: Path,
) -> None:
    with (default_assets / "vulture.toml").open("rb") as stream:
        settings = tomllib.load(stream)["tool"]["vulture"]

    assert not settings.keys() & {"make_whitelist", "sort_by_size", "verbose"}


def test_the_starter_config_references_bundled_or_generated_files(
    default_assets: Path,
) -> None:
    config = ConfigLoader(source=LocalYamlConfigSource()).load(
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
        if path.name == "astcheck.yaml":
            assert path.parent == default_assets
            continue
        assert path.is_file()
        assert path.resolve() in shipped_files
