"""Tests for :mod:`checksmith.runners.npx`."""

import pytest

from checksmith.runners.base import RunnerName
from checksmith.runners.npx import NpxRunner


@pytest.fixture
def runner() -> NpxRunner:
    return NpxRunner()


def test_the_runner_answers_to_its_config_name(runner: NpxRunner) -> None:
    assert runner.name is RunnerName.NPX


@pytest.mark.parametrize(
    "package",
    [
        "prettier@3.6.2",
        "prettier@^3.6.2",
        "prettier@~1.2",
        "prettier@>=1.0 <2",
        "prettier@1.x",
        "prettier@1.2.3 || >=2.0.0",
        "prettier@3.6.2-rc.1",
        "prettier@1.0.0+build.5",
        "@scope/name@1.0.0",
        "@scope/sub.name@^0.0.1",
    ],
)
def test_an_npm_name_at_a_constrained_range_is_accepted(
    package: str,
    runner: NpxRunner,
) -> None:
    runner.validate_package(package=package)


@pytest.mark.parametrize(
    "package",
    [
        "prettier",
        "@scope/name",
        "prettier@latest",
        "prettier@*",
        "prettier@x",
        "prettier@",
        "prettier@not a range",
        "github:user/repo",
        "file:../prettier",
        "https://example.com/p.tgz",
        "",
        "ruff==0.16.7",
    ],
)
def test_anything_that_is_not_a_constrained_npm_range_is_rejected(
    package: str,
    runner: NpxRunner,
) -> None:
    with pytest.raises(ValueError):
        runner.validate_package(package=package)


def test_a_range_is_accepted_where_only_a_pin_once_was(runner: NpxRunner) -> None:
    """``npx --package`` resolves a range, so Checksmith has no reason to refuse one."""
    runner.validate_package(package="prettier@^3.6.2")


def test_the_name_half_is_left_for_npm_to_judge(runner: NpxRunner) -> None:
    """npm owns the name grammar; restating it here would only age against it."""
    runner.validate_package(package="not a name@1.0.0")


def test_a_scoped_name_keeps_its_scope_marker_out_of_the_range(
    runner: NpxRunner,
) -> None:
    """The last ``@`` separates the range, so a scope marker is never mistaken
    for it.
    """
    runner.validate_package(package="@scope/name@1.0.0")

    with pytest.raises(ValueError):
        runner.validate_package(package="@scope/name")


def test_an_npm_tag_is_rejected_as_a_range(runner: NpxRunner) -> None:
    with pytest.raises(ValueError, match="not a valid npm range"):
        runner.validate_package(package="prettier@latest")


@pytest.mark.parametrize("package", ["prettier@*", "prettier@", "prettier@x"])
def test_a_range_matching_every_version_is_rejected(
    package: str,
    runner: NpxRunner,
) -> None:
    """``*``, ``x`` and an empty range all normalise to ``*``, constraining nothing."""
    with pytest.raises(ValueError, match="must constrain a version"):
        runner.validate_package(package=package)


def test_an_npx_vector_pins_the_package_explicitly(runner: NpxRunner) -> None:
    """``--package`` keeps npx from resolving the command name itself."""
    argv = runner.build_argv(
        package="prettier@3.6.2",
        command="prettier",
        arguments=("--check", "."),
    )

    assert argv == (
        "npx",
        "--yes",
        "--package",
        "prettier@3.6.2",
        "prettier",
        "--check",
        ".",
    )


def test_an_npx_vector_passes_a_range_through_unexpanded(runner: NpxRunner) -> None:
    """``^3.6.2`` reaches npx as written, not as ``>=3.6.2 <4.0.0``."""
    argv = runner.build_argv(
        package="prettier@^3.6.2",
        command="prettier",
        arguments=("--check",),
    )

    assert argv == (
        "npx",
        "--yes",
        "--package",
        "prettier@^3.6.2",
        "prettier",
        "--check",
    )
