"""One command: how it is run, and how the program's own output is read."""

import logging
import os
import subprocess
import tomllib
from abc import ABC, abstractmethod
from collections.abc import Mapping
from pathlib import Path
from stat import S_ISREG

from checksmith.config import Check
from checksmith.dtos import CheckResult, CheckStatus, CommandName, PackageType
from checksmith.errors import (
    CheckExecutionError,
    CheckOutputError,
    CheckPrerequisiteError,
)

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
        return self._run_argv(check=check, project_root=project_root, argv=check.argv)

    def _run_argv(
        self,
        *,
        check: Check,
        project_root: Path,
        argv: tuple[str, ...],
    ) -> CheckResult:
        if check.package_type is PackageType.UV:
            self._require_uv_project(check=check, project_root=project_root)
        logger.debug("%s argv=%s cwd=%s", check.id, argv, project_root)
        try:
            completed = subprocess.run(
                argv,
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
        return self.process_response(
            check_id=check.id,
            project_root=project_root,
            exit_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )

    def _require_uv_project(self, *, check: Check, project_root: Path) -> None:
        for variable in ("UV_PROJECT", "UV_WORKING_DIR"):
            if os.environ.get(variable):
                raise CheckPrerequisiteError(
                    check_id=check.id,
                    command=self.name,
                    config_file=project_root / "pyproject.toml",
                    problem=(
                        f"Unset {variable}; Checksmith uses project_root "
                        "to select the uv project and working directory"
                    ),
                )
        no_sync = os.environ.get("UV_NO_SYNC")
        if no_sync is not None and no_sync.lower() not in {"0", "false", "no", "off"}:
            raise CheckPrerequisiteError(
                check_id=check.id,
                command=self.name,
                config_file=project_root / "pyproject.toml",
                problem="UV_NO_SYNC must be unset or false for locked uv execution",
            )
        for filename in ("pyproject.toml", "uv.lock"):
            config_file = project_root / filename
            try:
                if not S_ISREG(config_file.stat().st_mode):
                    raise OSError(f"Expected a regular file: {config_file}")
                with config_file.open("rb") as stream:
                    if filename == "pyproject.toml":
                        try:
                            document: Mapping[str, object] = tomllib.load(stream)
                        except (tomllib.TOMLDecodeError, UnicodeDecodeError) as error:
                            raise ValueError(
                                f"invalid pyproject.toml: {error}"
                            ) from error
                        tool = document.get("tool")
                        uv = tool.get("uv") if isinstance(tool, Mapping) else None
                        if isinstance(uv, Mapping) and uv.get("managed") is False:
                            raise ValueError(
                                "uv execution requires tool.uv.managed=true"
                            )
                    else:
                        stream.read(1)
            except (OSError, ValueError) as error:
                raise CheckPrerequisiteError(
                    check_id=check.id,
                    command=self.name,
                    config_file=config_file,
                    problem=(
                        "uv execution requires readable pyproject.toml and uv.lock "
                        f"files in the project root: {error}"
                    ),
                ) from error

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
