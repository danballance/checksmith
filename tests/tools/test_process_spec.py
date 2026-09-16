"""Tests for :mod:`checksmith.tools.process_spec`."""

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from checksmith.config import Check, Config
from checksmith.tools.process_spec import ProcessSpec

CONFIG_PATH = Path("/workspace/project/.checksmith/checksmith.yaml")
PROJECT_ROOT = Path("/workspace/project")
RUFF_CONFIG = "/workspace/project/.checksmith/ruff.toml"


def build(**overrides: Any) -> Check:
    """One check, resolved the only way a check can be built."""
    body: dict[str, Any] = {
        "id": "ruff",
        "runner": "uvx",
        "package": "ruff==0.16.7",
        "command": "ruff",
        "args": ["check", "--config", RUFF_CONFIG, "."],
    }
    body.update(overrides)
    return Config.from_mapping(
        document={"schema_version": 1, "checks": [body]},
        config_path=CONFIG_PATH,
    ).checks[0]


@pytest.fixture
def ruff_check() -> Check:
    return build()


@pytest.fixture
def prettier_check() -> Check:
    """The npm example from the configuration contract."""
    return build(
        id="prettier",
        runner="npx",
        package="prettier@3.6.2",
        command="prettier",
        args=["--check", "."],
    )


def test_a_uvx_check_builds_the_documented_vector(ruff_check: Check) -> None:
    spec = ProcessSpec.from_check(check=ruff_check, project_root=PROJECT_ROOT)

    assert spec.argv == (
        "uvx",
        "--from",
        "ruff==0.16.7",
        "ruff",
        "check",
        "--config",
        RUFF_CONFIG,
        ".",
    )


def test_an_npx_check_builds_the_documented_vector(prettier_check: Check) -> None:
    spec = ProcessSpec.from_check(check=prettier_check, project_root=PROJECT_ROOT)

    assert spec.argv == (
        "npx",
        "--yes",
        "--package",
        "prettier@3.6.2",
        "prettier",
        "--check",
        ".",
    )


@pytest.mark.parametrize("check_name", ["ruff_check", "prettier_check"])
def test_every_check_runs_in_the_project_root(
    check_name: str,
    request: pytest.FixtureRequest,
) -> None:
    check: Check = request.getfixturevalue(check_name)

    spec = ProcessSpec.from_check(check=check, project_root=PROJECT_ROOT)

    assert spec.working_directory == PROJECT_ROOT


def test_a_check_with_no_arguments_still_builds_a_runnable_vector() -> None:
    spec = ProcessSpec.from_check(check=build(args=[]), project_root=PROJECT_ROOT)

    assert spec.argv == ("uvx", "--from", "ruff==0.16.7", "ruff")


def test_an_argument_containing_spaces_stays_one_element() -> None:
    """The vector is never joined into a string, so nothing needs quoting."""
    check = build(args=["--config", "/my project/ruff.toml"])

    spec = ProcessSpec.from_check(check=check, project_root=PROJECT_ROOT)

    assert spec.argv[-1] == "/my project/ruff.toml"


def test_no_field_of_a_process_spec_can_be_reassigned(ruff_check: Check) -> None:
    spec = ProcessSpec.from_check(check=ruff_check, project_root=PROJECT_ROOT)

    for field in ProcessSpec.model_fields:
        with pytest.raises(ValidationError):
            setattr(spec, field, ())


def test_a_process_spec_round_trips_through_json(ruff_check: Check) -> None:
    spec = ProcessSpec.from_check(check=ruff_check, project_root=PROJECT_ROOT)

    assert ProcessSpec.model_validate_json(spec.model_dump_json()) == spec
