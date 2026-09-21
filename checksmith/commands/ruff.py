import logging
import os
from pathlib import Path
from typing import Final

from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError

from checksmith.commands.command import Command
from checksmith.dtos import CheckResult, CheckStatus, CommandName
from checksmith.errors import CheckOutputError

logger = logging.getLogger(__name__)


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
            status=CheckStatus.FAILED if findings else CheckStatus.PASSED,
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
