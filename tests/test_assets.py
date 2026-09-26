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
import yaml

from checksmith.commands.pyarchgraph import PyArchGraphCommand
from checksmith.config import Config, ConfigPath
from checksmith.dtos import CheckStatus, CommandName, PackageType
from tests.commands.test_pyarchgraph import HEALTHY_REPORT
from tests.conftest import FakeProcesses


@pytest.fixture
def default_assets() -> Iterator[Path]:
    """The starter directory, located without assuming a source checkout."""
    resource = files("checksmith") / "assets" / "default"
    with as_file(resource) as path:
        yield path


def test_the_starter_directory_ships_four_files(default_assets: Path) -> None:
    """Dot-entries are skipped: Ruff plants a cache beside any config it finds."""
    shipped = sorted(
        item.name for item in default_assets.iterdir() if not item.name.startswith(".")
    )

    assert shipped == ["checksmith.yaml", "coverage.toml", "ruff.toml", "semgrep.yaml"]


def test_the_starter_config_loads_through_the_real_loader(
    default_assets: Path,
) -> None:
    config = Config.from_path(
        config_file=default_assets / "checksmith.yaml",
        working_directory=default_assets,
    )

    # ``project_root: ../../..`` resolves against the config directory.
    assert config.project_root == default_assets.parents[2]
    assert tuple(check.id for check in config.checks) == (
        "ruff",
        "semgrep",
        "ty",
        "pytest",
        "pyarchgraph",
    )


def test_the_starter_config_references_only_files_it_ships_with(
    default_assets: Path,
) -> None:
    """A wheel whose packaged data stopped matching would ship a config
    pointing at a file that is not in the distribution.

    The loader resolves a ``config_path`` argument, so the test reads the answer
    rather than recomputing it: what it checks is the path the tool is handed.
    """
    config = Config.from_path(
        config_file=default_assets / "checksmith.yaml",
        working_directory=default_assets,
    )

    for check, filenames in zip(
        config.checks,
        (("ruff.toml",), ("semgrep.yaml",), (), ("coverage.toml",), ()),
        strict=True,
    ):
        paths = tuple(
            argument.config_path
            for argument in check.args
            if isinstance(argument, ConfigPath)
        )

        assert paths == tuple(default_assets / filename for filename in filenames)
        assert all(path.is_file() for path in paths)


def test_the_starter_config_builds_a_ruff_invocation(
    default_assets: Path,
) -> None:
    config = Config.from_path(
        config_file=default_assets / "checksmith.yaml",
        working_directory=default_assets,
    )

    assert config.checks[0].argv == (
        "uvx",
        "--from",
        "ruff==0.16.7",
        "ruff",
        "check",
        "--config",
        # Absolute, and so independent of the project root the tool runs in.
        str(default_assets / "ruff.toml"),
        "--output-format",
        "json",
        # Relative, and so read by ruff against that project root.
        ".",
    )


def test_the_starter_config_builds_a_semgrep_invocation(
    default_assets: Path,
) -> None:
    config = Config.from_path(
        config_file=default_assets / "checksmith.yaml",
        working_directory=default_assets,
    )

    assert config.checks[1].argv == (
        "uvx",
        "--from",
        "semgrep==1.176.1",
        "semgrep",
        "scan",
        "--config",
        str(default_assets / "semgrep.yaml"),
        "--json",
        "--error",
        "--strict",
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
        config_file=default_assets / "checksmith.yaml",
        working_directory=default_assets,
    )

    assert config.checks[0].command is CommandName.RUFF
    assert "--output-format" in config.checks[0].args
    assert "json" in config.checks[0].args
    assert config.checks[1].command is CommandName.SEMGREP
    assert "--json" in config.checks[1].args
    assert config.checks[2].command is CommandName.TY
    assert "--output-format" in config.checks[2].args
    assert "gitlab" in config.checks[2].args
    assert config.checks[4].command is CommandName.PYARCHGRAPH
    assert config.checks[4].args == (".",)


def test_the_starter_config_builds_a_ty_invocation(default_assets: Path) -> None:
    config = Config.from_path(
        config_file=default_assets / "checksmith.yaml",
        working_directory=default_assets,
    )

    assert config.checks[2].argv == (
        "uvx",
        "--from",
        "ty==0.0.80",
        "ty",
        "check",
        "--output-format",
        "gitlab",
        "--error-on-warning",
        ".",
    )


def test_the_starter_config_builds_a_project_pytest_invocation(
    default_assets: Path,
) -> None:
    config = Config.from_path(
        config_file=default_assets / "checksmith.yaml",
        working_directory=default_assets,
    )

    check = config.checks[3]
    assert check.command is CommandName.PYTEST
    assert check.package is None
    assert check.argv == (
        "uv",
        "run",
        "--locked",
        "pytest",
        "tests",
        "--cov=.",
        "--cov-config",
        str(default_assets / "coverage.toml"),
        "--cov-report=term-missing",
        "--cov-fail-under=90",
    )


def test_the_starter_config_builds_a_pyarchgraph_invocation(
    default_assets: Path,
) -> None:
    config = Config.from_path(
        config_file=default_assets / "checksmith.yaml",
        working_directory=default_assets,
    )

    assert config.checks[4].argv == (
        "uvx",
        "--from",
        (
            "pyarchgraph @ git+https://github.com/danballance/pyarchgraph"
            "@f35224ecbb2382b40b9d91c3f79fe35ff12f7d9d"
        ),
        "pyarchgraph",
        ".",
    )


def test_the_starter_pyarchgraph_check_reads_stdout(
    default_assets: Path,
    tmp_path: Path,
    processes: FakeProcesses,
) -> None:
    config = Config.from_path(
        config_file=default_assets / "checksmith.yaml",
        working_directory=default_assets,
    )
    check = config.checks[4]
    processes.stdout = HEALTHY_REPORT

    result = PyArchGraphCommand().run(check=check, project_root=tmp_path)

    assert result.status is CheckStatus.PASSED
    assert processes.started[0].argv == check.argv
    assert list(tmp_path.iterdir()) == []


def test_the_starter_checks_choose_their_execution_environments(
    default_assets: Path,
) -> None:
    config = Config.from_path(
        config_file=default_assets / "checksmith.yaml",
        working_directory=default_assets,
    )

    assert tuple(check.package_type for check in config.checks) == (
        PackageType.UVX,
        PackageType.UVX,
        PackageType.UVX,
        PackageType.UV,
        PackageType.UVX,
    )


def test_the_starter_ruff_configuration_is_a_standalone_one(
    default_assets: Path,
) -> None:
    """A standalone file uses ``[lint]``; ``[tool.ruff.lint]`` is pyproject's."""
    settings = tomllib.loads((default_assets / "ruff.toml").read_text(encoding="utf-8"))

    assert "lint" in settings
    assert "tool" not in settings


def test_the_starter_ruff_configuration_selects_its_rules_explicitly(
    default_assets: Path,
) -> None:
    """An upgrade must not be able to change what this gate enforces."""
    settings = tomllib.loads((default_assets / "ruff.toml").read_text(encoding="utf-8"))

    assert settings["lint"]["select"] != []
    assert settings["target-version"] == "py312"


def test_the_starter_semgrep_configuration_enforces_the_supplied_rules(
    default_assets: Path,
) -> None:
    settings = yaml.safe_load(
        (default_assets / "semgrep.yaml").read_text(encoding="utf-8")
    )
    keyword_rule, variadic_rule = settings["rules"]

    assert keyword_rule["id"] == "python-require-keyword-only-parameters"
    assert variadic_rule["id"] == "python-no-variadic-parameters"
    assert all(rule["languages"] == ["python"] for rule in settings["rules"])
    assert all(rule["severity"] == "ERROR" for rule in settings["rules"])
    assert keyword_rule["message"] == (
        "Declare parameters after a bare * so callers must pass them "
        "by name. Only self or cls may precede the *."
    )
    assert variadic_rule["message"] == (
        "Replace variadic parameters with explicitly named parameters. "
        "Neither *args nor **kwargs is permitted."
    )
    assert keyword_rule["patterns"] == [
        {"pattern": "def $FUNC(...):\n    ...\n"},
        {"pattern-not": "def $FUNC(*, ...):\n    ...\n"},
        {"pattern-not": "def $FUNC(self, *, ...):\n    ...\n"},
        {"pattern-not": "def $FUNC(cls, *, ...):\n    ...\n"},
        {"pattern-not": "def $FUNC():\n    ...\n"},
        {"pattern-not": "def $FUNC(self):\n    ...\n"},
        {"pattern-not": "def $FUNC(cls):\n    ...\n"},
    ]
    assert variadic_rule["pattern-either"] == [
        {"pattern": "def $FUNC(..., *$ARGS, ...):\n    ...\n"},
        {"pattern": "def $FUNC(..., **$KWARGS):\n    ...\n"},
    ]
