import logging
import os
from pathlib import Path
from typing import Final

from pydantic import BaseModel, ConfigDict, ValidationError

from checksmith.commands.command import Command
from checksmith.dtos import CheckResult, CheckStatus, CommandName
from checksmith.errors import CheckOutputError

logger = logging.getLogger(__name__)


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
            status=CheckStatus.FAILED if findings else CheckStatus.PASSED,
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
