import logging
import os
import re
from pathlib import Path
from stat import S_ISREG
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from checksmith.adapters.driven.execution.command import CapturedOutputCommand
from checksmith.adapters.driven.execution.prerequisites import (
    ProjectFiles,
    UvPrerequisites,
)
from checksmith.adapters.driven.execution.processes import (
    ProcessExecutor,
    ProcessOutput,
)
from checksmith.domain.config import Check
from checksmith.domain.errors import CheckOutputError, CheckPrerequisiteError
from checksmith.domain.models import CheckResult, CheckStatus, CommandName

logger = logging.getLogger(__name__)

VULTURE_FINDING: Final = re.compile(
    r"(?P<path>[^\0]+):(?P<line>[1-9][0-9]*): (?P<message>.+) "
    r"\((?P<confidence>[0-9]+)% confidence\)"
)
VULTURE_REPORTED: Final = frozenset({0, 3})
VULTURE_CONFIG_OPTION: Final = "--config"


class VultureDiagnostic(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    path: Path
    line: int = Field(ge=1, strict=True)
    message: str = Field(min_length=1)
    confidence: int = Field(ge=0, le=100, strict=True)


class VultureCommand(CapturedOutputCommand):
    def __init__(
        self,
        executor: ProcessExecutor,
        uv_prerequisites: UvPrerequisites,
        files: ProjectFiles,
    ) -> None:
        super().__init__(executor=executor, uv_prerequisites=uv_prerequisites)
        self._files = files

    @property
    def name(self) -> CommandName:
        return CommandName.VULTURE

    def run(
        self, *, check: Check, project_root: Path, output: ProcessOutput | None
    ) -> CheckResult:
        # Vulture ignores a --config that is not a file and runs with its
        # defaults, so a mistyped path would pass a check that never applied
        # the project's settings.
        for config_file in self._config_files(check=check, project_root=project_root):
            try:
                if not S_ISREG(
                    self._files.stat(path=config_file, follow_symlinks=True).st_mode
                ):
                    raise OSError(f"Expected a regular file: {config_file}")
            except OSError as error:
                raise CheckPrerequisiteError(
                    check_id=check.id,
                    command=self.name,
                    config_file=config_file,
                    problem=(
                        "vulture ignores a --config that is not a file and "
                        f"silently runs with its defaults instead: {error}"
                    ),
                ) from error
        return super().run(check=check, project_root=project_root, output=output)

    def _config_files(self, *, check: Check, project_root: Path) -> tuple[Path, ...]:
        """Every file the check's args pass to ``--config``, as vulture finds it.

        Vulture reads a relative path against its working directory, the project
        root. Its parser accepts any abbreviation of the option, such as
        ``--conf``, with the path either following or after ``=``, and reads no
        options after ``--``.
        """
        arguments = check.arguments
        files: list[Path] = []
        index = 0
        while index < len(arguments) and arguments[index] != "--":
            option, separator, value = arguments[index].partition("=")
            index += 1
            # Anything from --c to --config; no other vulture option starts --c.
            if len(option) < len("--c") or not VULTURE_CONFIG_OPTION.startswith(option):
                continue
            if not separator:
                if index == len(arguments):
                    # Vulture rejects the missing path itself, exiting 2.
                    break
                value = arguments[index]
                index += 1
            files.append(project_root / value)
        return tuple(files)

    def process_response(
        self,
        *,
        check_id: str,
        project_root: Path,
        exit_code: int,
        stdout: str,
        stderr: str,
    ) -> CheckResult:
        if exit_code not in VULTURE_REPORTED:
            raise CheckOutputError(
                check_id=check_id,
                command=self.name,
                summary=f"vulture exited {exit_code} rather than reporting findings",
                problem="\n".join(
                    output.strip() for output in (stdout, stderr) if output.strip()
                ),
            )
        try:
            diagnostics = tuple(
                self._read_diagnostic(line)
                for line in stdout.splitlines()
                if line.strip()
            )
        except ValueError as error:
            raise CheckOutputError(
                check_id=check_id,
                command=self.name,
                summary="vulture did not return the plain report this command reads",
                problem=(
                    "Neither the check's args nor its vulture config may enable "
                    "--make-whitelist, --sort-by-size or --verbose.\n"
                    f"{error}\n{stderr.strip()}"
                ).strip(),
            ) from error
        if exit_code == 3 and not diagnostics:
            raise CheckOutputError(
                check_id=check_id,
                command=self.name,
                summary="vulture exited 3 without reporting findings",
                problem=f"The report was empty.\n{stderr.strip()}".strip(),
            )
        if exit_code == 0 and diagnostics:
            raise CheckOutputError(
                check_id=check_id,
                command=self.name,
                summary="vulture exited 0 but reported findings",
                problem=f"{stdout.strip()}\n{stderr.strip()}".strip(),
            )
        findings = tuple(
            self.finding(diagnostic=diagnostic, project_root=project_root)
            for diagnostic in diagnostics
        )
        logger.debug("%s read %d vulture findings", check_id, len(findings))
        return CheckResult(
            check_id=check_id,
            status=CheckStatus.FAILED if findings else CheckStatus.PASSED,
            messages=findings,
        )

    def _read_diagnostic(self, line: str) -> VultureDiagnostic:
        match = VULTURE_FINDING.fullmatch(line)
        if match is None:
            raise ValueError(f"Unrecognized output line: {line!r}")
        return VultureDiagnostic(
            path=Path(match["path"]),
            line=int(match["line"]),
            message=match["message"],
            confidence=int(match["confidence"]),
        )

    def finding(self, *, diagnostic: VultureDiagnostic, project_root: Path) -> str:
        path = os.path.relpath(
            (project_root / diagnostic.path).resolve(), project_root.resolve()
        )
        return (
            f"{path}:{diagnostic.line} {diagnostic.message} "
            f"({diagnostic.confidence}% confidence)"
        )
