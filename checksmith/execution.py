"""Execution of the configured tools."""

from checksmith.outputs.checkoutput import CheckOutput
from checksmith.tools.tool import Tool


class Runner:
    """Runs configured tools and reports what they found."""

    def __init__(self, tools: tuple[Tool, ...]) -> None:
        self.tools = tools

    def check(self) -> CheckOutput:
        """Run every tool and collect its result."""
        if len(self.tools) == 0:
            raise ValueError("a run needs at least one tool; none were configured")
        return CheckOutput(results=tuple(tool.run() for tool in self.tools))
