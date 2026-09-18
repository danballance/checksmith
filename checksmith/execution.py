"""Execution of the configured tools."""

import logging

from checksmith.dtos import ToolResult
from checksmith.outputs.checkoutput import CheckOutput
from checksmith.tools.tool import Tool

logger = logging.getLogger(__name__)


class Runner:
    """Runs configured tools and reports what they found."""

    def __init__(self, tools: tuple[Tool, ...]) -> None:
        self.tools = tools

    def check(self) -> CheckOutput:
        """Run every tool and collect its result."""
        if len(self.tools) == 0:
            raise ValueError("a run needs at least one tool; none were configured")
        logger.debug("running %d tools", len(self.tools))
        results: list[ToolResult] = []
        for tool in self.tools:
            result = tool.run()
            logger.debug(
                "%s failed=%s findings=%d",
                result.name,
                result.failed,
                len(result.findings),
            )
            results.append(result)
        return CheckOutput(results=tuple(results))
