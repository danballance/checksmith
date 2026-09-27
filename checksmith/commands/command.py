"""One command: how it is run, and how the program's own output is read."""

import logging
import shlex
from abc import ABC, abstractmethod
from pathlib import Path

from checksmith.config import Check
from checksmith.dtos import CheckResult, CommandName, PackageType
from checksmith.errors import (
    CheckExecutionError,
    CheckOutputError,
)
from checksmith.prerequisites import UvPrerequisites
from checksmith.processes import ProcessExecutor

logger = logging.getLogger(__name__)


class Command(ABC):
    """How one command is run, and how the program's own output is read.

    One subclass per program, because reading Ruff's output has nothing in
    common with reading Prettier's. What they all share --- starting a process,
    and reporting under the id of the check that asked for it --- lives here.

    Implementations keep no per-check state, so one shared instance per command
    is enough: a check is a configuration of a command, not a command of its own. Two
    checks naming ``ruff`` are two configurations handled by the one
    :class:`RuffCommand`.
    """

    def __init__(
        self, executor: ProcessExecutor, uv_prerequisites: UvPrerequisites
    ) -> None:
        self._executor = executor
        self._uv_prerequisites = uv_prerequisites

    @property
    @abstractmethod
    def name(self) -> CommandName:
        """The config value that selects this command."""

    def check_is_runnable(self, *, check: Check, project_root: Path) -> bool:
        return True

    def run(self, *, check: Check, project_root: Path) -> CheckResult:
        """Run one check's process and convert what it returned.

        The check is a parameter rather than state, which is what lets one
        instance serve every check that names it. The project root comes
        separately because it belongs to the config as a whole.

        A vector and no shell, so the arguments a config file wrote reach the
        program exactly as written --- a path containing a space needs no
        escaping and gets none. ``stdin`` is closed rather than inherited: a
        tool that stops to ask a question should fail, not hang a gate that
        nobody is watching.
        """
        return self._run_argv(check=check, project_root=project_root, argv=check.argv)

    def _run_argv(
        self,
        *,
        check: Check,
        project_root: Path,
        argv: tuple[str, ...],
    ) -> CheckResult:
        if check.package_type is PackageType.UV:
            self._uv_prerequisites.validate(check=check, project_root=project_root)
        logger.debug("%s argv=%s cwd=%s", check.id, argv, project_root)
        logger.debug("%s command=%s cwd=%s", check.id, shlex.join(argv), project_root)
        try:
            completed = self._executor.run(
                check_id=check.id,
                argv=argv,
                cwd=project_root,
                heartbeat_interval_seconds=10.0,
            )
        except OSError as error:
            raise CheckExecutionError(
                check_id=check.id,
                program=argv[0],
                working_directory=project_root,
                problem=str(error),
            ) from error
        except UnicodeDecodeError as error:
            raise CheckOutputError(
                check_id=check.id,
                command=self.name,
                summary=f"{self.name} did not return UTF-8 output",
                problem=str(error),
            ) from error
        logger.debug(
            "%s exited %d, stdout %d chars, stderr %d chars",
            check.id,
            completed.returncode,
            len(completed.stdout),
            len(completed.stderr),
        )
        logger.debug("%s processing output", check.id)
        return self.process_response(
            check_id=check.id,
            project_root=project_root,
            exit_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )

    @abstractmethod
    def process_response(
        self,
        *,
        check_id: str,
        project_root: Path,
        exit_code: int,
        stdout: str,
        stderr: str,
    ) -> CheckResult:
        """Convert what the finished process returned into a Checksmith result.

        The last three parameters are the whole of what a process leaves behind,
        and they are passed separately rather than as one object so that a test
        can exercise a program's real output without starting anything. The
        check's id comes with them because the result is reported under it and
        the command itself holds no per-check state.

        The project root comes too, because a tool reporting absolute paths has
        to be read against something for a finding to be worth showing.

        An implementation reads whatever the check's arguments told the program
        to write. Nothing is appended to those arguments, so a config that asks
        for a format its command cannot read is a mismatch the implementation
        reports rather than repairs.

        A nonzero exit status is not by itself a coding standard violation: a
        tool reporting an unreadable configuration also exits nonzero. Telling
        the two apart is exactly what each implementation is for.
        """
