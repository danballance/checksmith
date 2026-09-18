"""Running the configured checks."""

import logging
from collections.abc import Mapping
from pathlib import Path

from checksmith.commands.command import Command
from checksmith.config import Check
from checksmith.dtos import CheckResult, CommandName
from checksmith.outputs.checkoutput import CheckOutput

logger = logging.getLogger(__name__)


class Runner:
    """Runs the configured checks and reports what they found.

    The checks are the work; the commands are the handlers that know how to do
    it. One command may serve any number of checks, so the two arrive
    separately and are paired here, check by check.
    """

    def __init__(
        self,
        checks: tuple[Check, ...],
        commands: Mapping[CommandName, Command],
        project_root: Path,
    ) -> None:
        self.checks = checks
        self.commands = commands
        self.project_root = project_root

    def check(self) -> CheckOutput:
        """Run every check through its command and collect the result."""
        if len(self.checks) == 0:
            raise ValueError("a run needs at least one check; none were configured")
        logger.debug("running %d checks", len(self.checks))
        results: list[CheckResult] = []
        for check in self.checks:
            command = self.commands[check.command]
            result = command.run(check=check, project_root=self.project_root)
            logger.debug(
                "%s failed=%s findings=%d",
                result.check_id,
                result.failed,
                len(result.findings),
            )
            results.append(result)
        return CheckOutput(results=tuple(results))
