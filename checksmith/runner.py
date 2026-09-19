"""Running the configured checks."""

import logging
from collections.abc import Mapping
from pathlib import Path

from checksmith.commands.command import Command
from checksmith.config import Check
from checksmith.dtos import CheckResult, CommandName, ErrorSeverity
from checksmith.errors import CheckExecutionError, CheckOutputError
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
            try:
                result = command.run(check=check, project_root=self.project_root)
            except (CheckExecutionError, CheckOutputError) as error:
                logger.debug("%s could not complete", check.id, exc_info=True)
                result = CheckResult(
                    check_id=check.id,
                    severity=ErrorSeverity.ERROR,
                    messages=(str(error),),
                )
            logger.debug(
                "%s severity=%s messages=%d",
                result.check_id,
                result.severity,
                len(result.messages),
            )
            results.append(result)
        return CheckOutput(results=tuple(results))
