"""Running the configured checks."""

import logging
from collections.abc import Mapping
from pathlib import Path

from checksmith.commands.command import Command
from checksmith.config import Check
from checksmith.dtos import CheckResult, CheckStatus, CommandName
from checksmith.errors import (
    CheckExecutionError,
    CheckOutputError,
    CheckPrerequisiteError,
)
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
        return self._run(preparing=False)

    def prepare(self) -> CheckOutput:
        """Prepare configured checks before coding begins."""
        return self._run(preparing=True)

    def _run(self, preparing: bool) -> CheckOutput:
        if len(self.checks) == 0:
            raise ValueError("a run needs at least one check; none were configured")
        logger.debug(
            "%s %d checks", "preparing" if preparing else "running", len(self.checks)
        )
        results: list[CheckResult] = []
        for index, check in enumerate(self.checks, start=1):
            logger.debug(
                "starting %s %d/%d: %s",
                "preparation" if preparing else "check",
                index,
                len(self.checks),
                check.id,
            )
            command = self.commands[check.command]
            try:
                if preparing:
                    result = command.prepare(
                        check=check, project_root=self.project_root
                    )
                elif command.check_is_runnable(
                    check=check, project_root=self.project_root
                ):
                    result = command.run(check=check, project_root=self.project_root)
                else:
                    result = CheckResult(
                        check_id=check.id,
                        status=CheckStatus.SKIPPED,
                        messages=("Check is not applicable to this project.",),
                    )
            except (
                CheckExecutionError,
                CheckOutputError,
                CheckPrerequisiteError,
            ) as error:
                logger.debug("%s could not complete", check.id, exc_info=True)
                result = CheckResult(
                    check_id=check.id,
                    status=CheckStatus.ERROR,
                    messages=(str(error),),
                )
            logger.debug(
                "%s status=%s messages=%d",
                result.check_id,
                result.status,
                len(result.messages),
            )
            results.append(result)
        return CheckOutput(results=tuple(results))
