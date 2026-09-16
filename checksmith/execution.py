"""Execution of the configured tools.

Named for what it does rather than for the class it holds, so that it does not
read as a sibling of :mod:`checksmith.runners` --- a different idea entirely:
how a package is fetched, not how checks are run.
"""

from typing import Protocol

from checksmith.outputs.check import Check
from checksmith.tools.tool import Tool


class CheckRunner(Protocol):
    """Something that runs a suite of checks and reports the outcome."""

    def check(self) -> Check:
        """Run every configured check and collect the results."""
        ...


class Runner:
    """Runs configured tools and reports what they found.

    A plain class rather than a Pydantic model: ``tuple[Tool, ...]`` is a
    Protocol, and Pydantic cannot build a schema for one. Turning that off with
    ``arbitrary_types_allowed`` would keep the syntax and lose the checking,
    which is the wrong trade for something that holds no data worth validating.
    """

    def __init__(self, tools: tuple[Tool, ...]) -> None:
        self.tools = tools

    def check(self) -> Check:
        """Run every tool and collect its result.

        Each tool owns its own execution; this only decides that they all run and
        that every result is kept.
        """
        if len(self.tools) == 0:
            # The schema requires at least one check, so an empty suite means a
            # caller built one by hand. Reporting success for nothing run is the
            # one outcome worse than failing here.
            raise ValueError("a run needs at least one tool; none were configured")
        return Check(results=tuple(tool.run() for tool in self.tools))
