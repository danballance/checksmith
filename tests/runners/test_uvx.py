"""Tests for :mod:`checksmith.runners.uvx`."""

import pytest

from checksmith.dtos import RunnerName
from checksmith.runners import UvxRunner


@pytest.fixture
def runner() -> UvxRunner:
    return UvxRunner()


def test_the_runner_answers_to_its_config_name(runner: UvxRunner) -> None:
    assert runner.name is RunnerName.UVX


@pytest.mark.parametrize(
    "package",
    [
        "ruff==0.16.7",
        "ruff>=0.14,<0.15",
        "mypy[faster-cache]==1.18.0",
        "ruff~=1.0",
        "ruff!=1.0",
        "ruff==1.*",
        "ruff===1.0",
        "Some.Pkg==1.0",
        "some_pkg==1.0",
        "ruff == 1.0",
        "ruff==1!2.0",
        "ruff==1.0+local",
        "ruff==3.6.2rc1",
        'ruff==1.0; python_version < "3.9"',
        "a==1",
    ],
)
def test_a_python_requirement_with_a_version_is_accepted(
    package: str,
    runner: UvxRunner,
) -> None:
    runner.validate_package(package=package)


@pytest.mark.parametrize(
    "package",
    [
        "ruff",
        "ruff==",
        "==1.0",
        "",
        "./local-ruff",
        "git+https://example.com/ruff.git",
        "ruff @ git+https://example.com/ruff.git",
        "@scope/name@1.0.0",
        "prettier@3.6.2",
    ],
)
def test_anything_that_is_not_a_versioned_requirement_is_rejected(
    package: str,
    runner: UvxRunner,
) -> None:
    with pytest.raises(ValueError):
        runner.validate_package(package=package)


def test_a_range_is_accepted_where_only_a_pin_once_was(runner: UvxRunner) -> None:
    """``uvx --from`` resolves a range, so Checksmith has no reason to refuse one."""
    runner.validate_package(package="ruff>=0.14,<0.15")


def test_extras_travel_with_the_requirement(runner: UvxRunner) -> None:
    """``packaging`` owns the grammar, so extras need no handling of their own."""
    runner.validate_package(package="mypy[faster-cache]==1.18.0")


def test_a_requirement_naming_no_version_is_rejected(runner: UvxRunner) -> None:
    """A bare name resolves to whatever is newest today, which is not a gate."""
    with pytest.raises(ValueError, match="must constrain a version"):
        runner.validate_package(package="ruff")


def test_a_url_requirement_is_rejected_because_it_names_no_version(
    runner: UvxRunner,
) -> None:
    """PEP 508 forbids a URL and a specifier together, so the version rule
    catches it.
    """
    with pytest.raises(ValueError, match="must constrain a version"):
        runner.validate_package(package="ruff @ git+https://example.com/ruff.git")


def test_an_npm_specification_is_not_a_python_requirement(runner: UvxRunner) -> None:
    """``packaging`` reads this as the package ``prettier`` at the URL ``3.6.2``."""
    with pytest.raises(ValueError, match="must constrain a version"):
        runner.validate_package(package="prettier@3.6.2")


def test_a_requirement_that_does_not_parse_is_rejected(runner: UvxRunner) -> None:
    with pytest.raises(ValueError, match="not a valid Python requirement"):
        runner.validate_package(package="./local-ruff")


def test_a_uvx_vector_names_the_package_before_the_command(runner: UvxRunner) -> None:
    argv = runner.build_argv(
        package="ruff==0.16.7",
        command="ruff",
        arguments=("check", "."),
    )

    assert argv == ("uvx", "--from", "ruff==0.16.7", "ruff", "check", ".")


def test_a_uvx_vector_passes_a_range_through_unchanged(runner: UvxRunner) -> None:
    """The command that runs is the one the config file names, character for character."""
    argv = runner.build_argv(
        package="ruff>=0.14,<0.15",
        command="ruff",
        arguments=("check", "."),
    )

    assert argv == ("uvx", "--from", "ruff>=0.14,<0.15", "ruff", "check", ".")
