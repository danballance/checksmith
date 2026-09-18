"""One command: how it is run, and how the program's own output is read."""

import logging
from abc import ABC, abstractmethod
from collections.abc import Mapping
from pathlib import Path

from checksmith.config import Check
from checksmith.dtos import CheckResult, CommandName

logger = logging.getLogger(__name__)


class Command(ABC):
    """How one command is run, and how the program's own output is read.

    One subclass per program, because reading Ruff's output has nothing in
    common with reading Prettier's. What they all share --- starting a process,
    and reporting under the id of the check that asked for it --- lives here.

    Implementations are stateless, so one shared instance per command is enough:
    a check is a configuration of a command, not a command of its own. Two
    checks naming ``ruff`` are two configurations handled by the one
    :class:`RuffCommand`.
    """

    @property
    @abstractmethod
    def name(self) -> CommandName:
        """The config value that selects this command."""

    def run(self, *, check: Check, project_root: Path) -> CheckResult:
        """Run one check's process and convert what it returned.

        The check is a parameter rather than state, which is what lets one
        instance serve every check that names it. The project root comes
        separately because it belongs to the config as a whole.

        Not implemented yet. Returning a passing :class:`CheckResult` would be
        worse than failing: it would report success for a check that never ran.
        """
        logger.debug("%s argv=%s cwd=%s", check.id, check.argv, project_root)
        raise NotImplementedError(
            f"Check execution is not implemented yet; cannot run check: {check.id}"
        )

    @abstractmethod
    def process_response(
        self,
        *,
        check_id: str,
        exit_code: int,
        stdout: str,
        stderr: str,
    ) -> CheckResult:
        """Convert what the finished process returned into a Checksmith result.

        The last three parameters are the whole of what a process leaves behind,
        and they are passed separately rather than as one object so that a test
        can exercise a program's real output without starting anything. The
        check's id comes with them because the result is reported under it and
        the command itself holds no state.

        A nonzero exit status is not by itself a coding standard violation: a
        tool reporting an unreadable configuration also exits nonzero. Telling
        the two apart is exactly what each implementation is for.
        """

    @classmethod
    def for_name(cls, *, name: CommandName) -> Command:
        """Return the one command that answers to ``name``.

        A ``match`` rather than a mapping lookup: a type checker proves this
        covers every member of :class:`CommandName`, so adding a fourth command
        is an error reported here at check time rather than a ``KeyError`` in
        front of a user.
        """
        match name:
            case CommandName.RUFF:
                return RuffCommand()

    @classmethod
    def registry(cls) -> Mapping[CommandName, Command]:
        """Every command Checksmith ships, keyed by the config value that names it.

        Built from the enum rather than written out, so the mapping is total: a
        check's ``command`` field has already been validated against the same
        members, and so can never miss.
        """
        return {name: cls.for_name(name=name) for name in CommandName}


class RuffCommand(Command):
    """Ruff, a Python linter and formatter."""

    @property
    def name(self) -> CommandName:
        return CommandName.RUFF

    def process_response(
        self,
        *,
        check_id: str,
        exit_code: int,
        stdout: str,
        stderr: str,
    ) -> CheckResult:
        raise NotImplementedError(
            f"Reading ruff output is not implemented yet; check: {check_id}"
        )
