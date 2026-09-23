"""One command: how it is run, and how the program's own output is read."""

import logging
import subprocess
from abc import ABC, abstractmethod
from pathlib import Path

from checksmith.config import Check
from checksmith.dtos import CheckResult, CheckStatus, CommandName
from checksmith.errors import CheckExecutionError, CheckOutputError

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

    def check_is_runnable(self, *, check: Check, project_root: Path) -> bool:
        return True

    def prepare(self, *, check: Check, project_root: Path) -> CheckResult:
        return CheckResult(
            check_id=check.id,
            status=CheckStatus.SKIPPED,
            messages=("This command does not require preparation.",),
        )

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
        logger.debug("%s argv=%s cwd=%s", check.id, check.argv, project_root)
        try:
            completed = subprocess.run(
                check.argv,
                # Stringified here rather than left to the standard library:
                # when the root is the thing that is missing, the operating
                # system quotes what it was handed, and a ``PosixPath(...)``
                # repr in a diagnostic is Checksmith's noise, not the OS's.
                cwd=str(project_root),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                # No ``errors`` override: a tool emitting bytes that are not
                # UTF-8 fails here rather than having its output patched into
                # something ``process_response`` would go on to misread.
                encoding="utf-8",
                # A linter exits nonzero for the ordinary reason that it found
                # something. Raising would make every failing check an error,
                # which is the distinction ``process_response`` exists to draw.
                check=False,
            )
        except OSError as error:
            # The program is missing, or the root is. Either way nothing ran,
            # and the check's id is the only thing that says which one.
            raise CheckExecutionError(
                check_id=check.id,
                program=check.argv[0],
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
        the command itself holds no state.

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
