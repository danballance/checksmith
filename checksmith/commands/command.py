"""One command: how it is run, and how the program's own output is read."""

import logging
import os
import subprocess
from abc import ABC, abstractmethod
from collections.abc import Mapping
from pathlib import Path
from typing import Final

from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError

from checksmith.config import Check
from checksmith.dtos import CheckResult, CommandName, ErrorSeverity
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
            case CommandName.SEMGREP:
                return SemgrepCommand()

    @classmethod
    def registry(cls) -> Mapping[CommandName, Command]:
        """Every command Checksmith ships, keyed by the config value that names it.

        Built from the enum rather than written out, so the mapping is total: a
        check's ``command`` field has already been validated against the same
        members, and so can never miss.
        """
        return {name: cls.for_name(name=name) for name in CommandName}


class RuffLocation(BaseModel):
    """Where in a file one ruff diagnostic starts."""

    model_config = ConfigDict(frozen=True)

    row: int
    column: int


class RuffDiagnostic(BaseModel):
    """One diagnostic from ruff's JSON reporter, as far as Checksmith reads it.

    Ruff reports a fix, a rule URL, a severity, an end location and more besides.
    Those are ignored rather than rejected, so that a release adding a twelfth
    key does not take the gate down. That is not a fallback: every field named
    here is required, and a diagnostic missing one is refused.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    code: str | None
    """``None`` for a diagnostic that is not a rule, such as a syntax error."""

    message: str
    filename: Path
    location: RuffLocation


RUFF_DIAGNOSTICS: Final = TypeAdapter(tuple[RuffDiagnostic, ...])
"""Ruff's whole report: a JSON array, empty when it found nothing."""

RUFF_REPORTED: Final = frozenset({0, 1})
"""Exit codes on which ruff wrote a report. Anything else is ruff itself failing."""

RUFF_FORMAT_ARGUMENTS: Final = "--output-format json"
"""What a check's own arguments must contain for this command to read it."""


class RuffCommand(Command):
    """Ruff, a Python linter and formatter, read through its JSON reporter."""

    @property
    def name(self) -> CommandName:
        return CommandName.RUFF

    def process_response(
        self,
        *,
        check_id: str,
        project_root: Path,
        exit_code: int,
        stdout: str,
        stderr: str,
    ) -> CheckResult:
        """Read ruff's JSON reporter.

        Ruff exits 0 having found nothing, 1 having found something, and 2
        having failed --- an unreadable ``ruff.toml``, an argument it does not
        know. Only the first two are a report, and separating them from the
        third is the distinction this method exists to draw: a broken tool is
        not a coding standard violation, and reporting it as one would make the
        gate stop meaning what it says.
        """
        if exit_code not in RUFF_REPORTED:
            raise CheckOutputError(
                check_id=check_id,
                command=self.name,
                summary=f"ruff exited {exit_code} rather than reporting findings",
                problem=stderr.strip(),
            )
        try:
            diagnostics = RUFF_DIAGNOSTICS.validate_json(stdout)
        except ValidationError as error:
            # One catch for output that is not JSON and for JSON of the wrong
            # shape: ``validate_json`` does both at once, and the config author
            # fixes either the same way.
            raise CheckOutputError(
                check_id=check_id,
                command=self.name,
                summary="ruff did not return the JSON this command reads",
                problem=(
                    # The actionable line first: what follows it is Pydantic
                    # saying where the document stopped making sense, which
                    # matters for a shape mismatch and is noise for a format one.
                    f"The check's args must contain {RUFF_FORMAT_ARGUMENTS!r}.\n"
                    f"{error}"
                ),
            ) from error
        findings = tuple(
            self.finding(diagnostic=diagnostic, project_root=project_root)
            for diagnostic in diagnostics
        )
        logger.debug("%s read %d ruff diagnostics", check_id, len(findings))
        return CheckResult(
            check_id=check_id,
            # The findings are the report. Separating a report from a failure
            # was the exit code's whole job here, and it has done it.
            severity=ErrorSeverity.FAILURE if findings else None,
            messages=findings,
        )

    def finding(self, *, diagnostic: RuffDiagnostic, project_root: Path) -> str:
        """Render one diagnostic, its path relative to the project root."""
        # Resolved on both sides because ruff resolves symlinks in what it
        # reports: a root of ``/tmp/x`` comes back as ``/private/tmp/x`` on
        # macOS, and a lexical answer would read ``../../private/tmp/x/app.py``.
        # ``os.path.relpath`` rather than ``Path.relative_to`` because a file
        # outside the root should read as ``../elsewhere.py``, not raise.
        path = os.path.relpath(diagnostic.filename.resolve(), project_root.resolve())
        code = "" if diagnostic.code is None else f" {diagnostic.code}"
        location = diagnostic.location
        return f"{path}:{location.row}:{location.column}{code} {diagnostic.message}"


class SemgrepLocation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    line: int
    col: int


class SemgrepMessage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    message: str


class SemgrepDiagnostic(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    check_id: str
    path: Path
    start: SemgrepLocation
    extra: SemgrepMessage


class SemgrepReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    results: tuple[SemgrepDiagnostic, ...]
    errors: tuple[SemgrepMessage, ...]


SEMGREP_REPORTED: Final = frozenset({0, 1})


class SemgrepCommand(Command):
    @property
    def name(self) -> CommandName:
        return CommandName.SEMGREP

    def process_response(
        self,
        *,
        check_id: str,
        project_root: Path,
        exit_code: int,
        stdout: str,
        stderr: str,
    ) -> CheckResult:
        if exit_code not in SEMGREP_REPORTED:
            raise CheckOutputError(
                check_id=check_id,
                command=self.name,
                summary=f"semgrep exited {exit_code} rather than reporting findings",
                problem=stderr.strip(),
            )
        try:
            report = SemgrepReport.model_validate_json(stdout)
        except ValidationError as error:
            raise CheckOutputError(
                check_id=check_id,
                command=self.name,
                summary="semgrep did not return the JSON this command reads",
                problem=(
                    "The check's args must contain '--json'.\n"
                    f"{error}\n{stderr.strip()}"
                ).strip(),
            ) from error
        if report.errors:
            raise CheckOutputError(
                check_id=check_id,
                command=self.name,
                summary="semgrep reported scan errors",
                problem="\n".join(error.message for error in report.errors),
            )
        findings = tuple(
            self.finding(diagnostic=diagnostic, project_root=project_root)
            for diagnostic in report.results
        )
        logger.debug("%s read %d semgrep diagnostics", check_id, len(findings))
        return CheckResult(
            check_id=check_id,
            severity=ErrorSeverity.FAILURE if findings else None,
            messages=findings,
        )

    def finding(self, *, diagnostic: SemgrepDiagnostic, project_root: Path) -> str:
        path = os.path.relpath(
            (project_root / diagnostic.path).resolve(), project_root.resolve()
        )
        return (
            f"{path}:{diagnostic.start.line}:{diagnostic.start.col}"
            f" {diagnostic.check_id} {diagnostic.extra.message}"
        )
