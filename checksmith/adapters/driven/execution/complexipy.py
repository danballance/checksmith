import logging
import os
import re
from pathlib import Path
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from checksmith.adapters.driven.execution.command import CapturedOutputCommand
from checksmith.adapters.driven.execution.processes import ProcessOutput
from checksmith.domain.config import Check
from checksmith.domain.errors import CheckOutputError
from checksmith.domain.models import CheckResult, CheckStatus, CommandName

logger = logging.getLogger(__name__)

REMOVABLE_IGNORES_HEADER: Final = (
    "The following ignore comment(s) are no longer necessary "
    "(complexity is within the allowed limit) and can be removed:"
)
REMOVABLE_IGNORE: Final = re.compile(
    r".+:[1-9][0-9]*  function=\S+ complexity=[0-9]+  "
    r"# (?:complexipy: ignore|noqa: complexipy)"
)
CONFIG_ERROR_PREFIXES: Final = ("Failed to parse ", "Invalid config in ")


class ComplexipyDiagnostic(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    path: Path
    function: str = Field(min_length=1)
    complexity: int = Field(ge=0, strict=True)


class ComplexipyCommand(CapturedOutputCommand):
    @property
    def name(self) -> CommandName:
        return CommandName.COMPLEXIPY

    def run(
        self, *, check: Check, project_root: Path, output: ProcessOutput | None
    ) -> CheckResult:
        arguments = check.arguments
        if "--" in arguments:
            arguments = arguments[: arguments.index("--")]
        for flag in ("--plain", "--failed"):
            values = tuple(
                argument
                for argument in arguments
                if argument == flag or argument.startswith(f"{flag}=")
            )
            if len(values) != 1 or values[0] not in (flag, f"{flag}=true"):
                raise CheckOutputError(
                    check_id=check.id,
                    command=self.name,
                    summary=(
                        "complexipy requires plain output containing only "
                        "failed functions"
                    ),
                    problem=(
                        "The check's args must enable --plain --failed exactly "
                        "once, using bare flags or '=true', before '--'."
                    ),
                )
        return super().run(check=check, project_root=project_root, output=output)

    def process_response(
        self,
        *,
        check_id: str,
        project_root: Path,
        exit_code: int,
        stdout: str,
        stderr: str,
    ) -> CheckResult:
        output = "\n".join(
            stream.strip() for stream in (stdout, stderr) if stream.strip()
        )
        if exit_code not in (0, 1):
            raise CheckOutputError(
                check_id=check_id,
                command=self.name,
                summary=f"complexipy exited {exit_code} rather than reporting findings",
                problem=output,
            )
        if any(line.startswith(CONFIG_ERROR_PREFIXES) for line in stderr.splitlines()):
            raise CheckOutputError(
                check_id=check_id,
                command=self.name,
                summary="complexipy reported configuration errors",
                problem=output,
            )
        try:
            diagnostics = self._read_report(stdout)
        except ValueError as error:
            raise CheckOutputError(
                check_id=check_id,
                command=self.name,
                summary="complexipy did not return the plain output this command reads",
                problem=(
                    "The check's args must contain '--plain --failed'.\n"
                    f"{error}\n{output}"
                ).strip(),
            ) from error
        if exit_code == 1 and not diagnostics:
            raise CheckOutputError(
                check_id=check_id,
                command=self.name,
                summary="complexipy exited 1 without reporting findings",
                problem=f"The report was empty.\n{output}".strip(),
            )
        findings = tuple(
            self.finding(diagnostic=diagnostic, project_root=project_root)
            for diagnostic in diagnostics
        )
        logger.debug("%s read %d complexipy diagnostics", check_id, len(findings))
        return CheckResult(
            check_id=check_id,
            status=CheckStatus.FAILED if findings else CheckStatus.PASSED,
            messages=findings,
        )

    def _read_report(self, stdout: str) -> tuple[ComplexipyDiagnostic, ...]:
        lines = tuple(line for line in stdout.splitlines() if line.strip())
        if REMOVABLE_IGNORES_HEADER in lines:
            header_index = lines.index(REMOVABLE_IGNORES_HEADER)
            advisories = lines[header_index + 1 :]
            if not advisories or any(
                REMOVABLE_IGNORE.fullmatch(line) is None for line in advisories
            ):
                raise ValueError("Invalid complexipy removable-ignore advisory section")
            lines = lines[:header_index]
        return tuple(self._read_diagnostic(line) for line in lines)

    def _read_diagnostic(self, line: str) -> ComplexipyDiagnostic:
        fields = line.rsplit(maxsplit=2)
        if len(fields) != 3:
            raise ValueError(f"Unrecognized output line: {line!r}")
        path, function, complexity = fields
        if not complexity.isascii() or not complexity.isdecimal() or "\0" in path:
            raise ValueError(f"Invalid complexity record: {line!r}")
        return ComplexipyDiagnostic(
            path=Path(path), function=function, complexity=int(complexity)
        )

    def finding(self, *, diagnostic: ComplexipyDiagnostic, project_root: Path) -> str:
        path = os.path.relpath(
            (project_root / diagnostic.path).resolve(), project_root.resolve()
        )
        return (
            f"{path}: {diagnostic.function} has cognitive complexity "
            f"{diagnostic.complexity}."
        )
