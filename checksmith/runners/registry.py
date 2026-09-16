"""Finding the runner a config file's ``runner:`` value names."""

from typing import Final

from checksmith.runners.base import PackageRunner, RunnerName
from checksmith.runners.npx import NpxRunner
from checksmith.runners.uvx import UvxRunner

UVX_RUNNER: Final = UvxRunner()
NPX_RUNNER: Final = NpxRunner()


def runner_for(*, name: RunnerName) -> PackageRunner:
    """Return the one runner that answers to ``name``.

    A ``match`` rather than a mapping lookup: a type checker proves this covers
    every member of :class:`RunnerName`, so adding a third runner is an error
    reported here at check time rather than a ``KeyError`` in front of a user.
    """
    match name:
        case RunnerName.UVX:
            return UVX_RUNNER
        case RunnerName.NPX:
            return NPX_RUNNER
