"""Tests for :mod:`checksmith.runners.registry`."""

import pytest

from checksmith.runners.base import PackageRunner, RunnerName
from checksmith.runners.registry import runner_for


@pytest.mark.parametrize("name", list(RunnerName))
def test_every_runner_name_resolves_to_the_runner_that_owns_it(
    name: RunnerName,
) -> None:
    """Belt and braces: a type checker also proves the match is exhaustive."""
    runner = runner_for(name=name)

    assert isinstance(runner, PackageRunner)
    assert runner.name is name


def test_the_same_instance_is_returned_every_time() -> None:
    """Runners are stateless, so one shared instance per name is enough."""
    assert runner_for(name=RunnerName.UVX) is runner_for(name=RunnerName.UVX)
