import logging
import os
from pathlib import Path
from typing import Final

from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError

from checksmith.commands.command import Command
from checksmith.dtos import CheckResult, CheckStatus, CommandName
from checksmith.errors import CheckOutputError

logger = logging.getLogger(__name__)


class TyPosition(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    line: int
    column: int


class TyPositions(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    begin: TyPosition


class TyLocation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    path: Path
    positions: TyPositions


class TyDiagnostic(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    description: str
    location: TyLocation


TY_DIAGNOSTICS: Final = TypeAdapter(tuple[TyDiagnostic, ...])
TY_REPORTED: Final = frozenset({0, 1})
TY_FORMAT_ARGUMENTS: Final = "--output-format gitlab"


class TyCommand(Command):
    @property
    def name(self) -> CommandName:
        return CommandName.TY

    def process_response(
        self,
        *,
        check_id: str,
        project_root: Path,
        exit_code: int,
        stdout: str,
        stderr: str,
    ) -> CheckResult:
        if exit_code not in TY_REPORTED:
            raise CheckOutputError(
                check_id=check_id,
                command=self.name,
                summary=f"ty exited {exit_code} rather than reporting findings",
                problem="\n".join(
                    output.strip() for output in (stdout, stderr) if output.strip()
                ),
            )
        try:
            diagnostics = TY_DIAGNOSTICS.validate_json(stdout)
        except ValidationError as error:
            raise CheckOutputError(
                check_id=check_id,
                command=self.name,
                summary="ty did not return the GitLab JSON this command reads",
                problem=(
                    f"The check's args must contain {TY_FORMAT_ARGUMENTS!r}.\n"
                    f"{error}\n{stderr.strip()}"
                ).strip(),
            ) from error
        if exit_code == 1 and not diagnostics:
            raise CheckOutputError(
                check_id=check_id,
                command=self.name,
                summary="ty exited 1 without reporting findings",
                problem=f"The report was empty.\n{stderr.strip()}".strip(),
            )
        findings = tuple(
            self.finding(diagnostic=diagnostic, project_root=project_root)
            for diagnostic in diagnostics
        )
        logger.debug("%s read %d ty diagnostics", check_id, len(findings))
        return CheckResult(
            check_id=check_id,
            status=CheckStatus.FAILED if findings else CheckStatus.PASSED,
            messages=findings,
        )

    def finding(self, *, diagnostic: TyDiagnostic, project_root: Path) -> str:
        location = diagnostic.location
        path = os.path.relpath(
            (project_root / location.path).resolve(), project_root.resolve()
        )
        begin = location.positions.begin
        return f"{path}:{begin.line}:{begin.column} {diagnostic.description}"
