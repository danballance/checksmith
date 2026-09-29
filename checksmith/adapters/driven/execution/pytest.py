from importlib.resources.abc import Traversable
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Annotated, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from checksmith.adapters.driven.execution.command import Command
from checksmith.adapters.driven.execution.packages import PackageInvocation
from checksmith.adapters.driven.execution.prerequisites import UvPrerequisites
from checksmith.adapters.driven.execution.processes import ProcessExecutor
from checksmith.application.ports.execution import ProcessOutput
from checksmith.domain.config import Check
from checksmith.domain.errors import CheckOutputError
from checksmith.domain.models import CheckResult, CheckStatus, CommandName


class _SummaryModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class PytestFailure(_SummaryModel):
    nodeid: str
    phase: Literal["collect", "setup", "call", "teardown"]
    reason: str


class PytestCoverage(_SummaryModel):
    total: Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)] | None
    minimum: Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)] | None


class PytestSummary(_SummaryModel):
    outcomes: dict[str, Annotated[int, Field(ge=0)]]
    duration_seconds: Annotated[float, Field(ge=0, allow_inf_nan=False)]
    warnings: Annotated[int, Field(ge=0)]
    deselected: Annotated[int, Field(ge=0)]
    failures: tuple[PytestFailure, ...]
    coverage: PytestCoverage | None

    def messages(self) -> tuple[str, ...]:
        order = ("failed", "passed", "skipped", "xfailed", "xpassed", "error")
        categories = (*order, *sorted(self.outcomes.keys() - set(order)))
        counts = [
            f"{self.outcomes[category]} {category}"
            + ("s" if category == "error" and self.outcomes[category] != 1 else "")
            for category in categories
            if self.outcomes.get(category, 0)
        ]
        if self.warnings:
            counts.append(
                f"{self.warnings} warning" + ("s" if self.warnings != 1 else "")
            )
        if self.deselected:
            counts.append(f"{self.deselected} deselected")
        outcome = ", ".join(counts) if counts else "No tests ran"
        messages = [f"{outcome} in {self.duration_seconds:.2f}s"]
        for failure in self.failures:
            label = "FAILED" if failure.phase == "call" else "ERROR"
            phase = "" if failure.phase == "call" else f" ({failure.phase})"
            messages.append(f"{label} {failure.nodeid}{phase}: {failure.reason}")
        if self.coverage is not None:
            total = self.coverage.total
            coverage = (
                "Total coverage: not reported"
                if total is None
                else f"Total coverage: {total:.2f}%"
            )
            if self.coverage.minimum is not None:
                coverage += f" (required: {self.coverage.minimum:.2f}%)"
            messages.append(coverage)
        return tuple(messages)


class LauncherSource(Protocol):
    def read(self) -> str: ...


class PackagedLauncherSource:
    def __init__(self, resource: Traversable) -> None:
        self._resource = resource

    def read(self) -> str:
        return self._resource.read_text(encoding="utf-8")


class PytestCommand(Command):
    def __init__(
        self,
        executor: ProcessExecutor,
        uv_prerequisites: UvPrerequisites,
        source: LauncherSource,
        package: PackageInvocation,
    ) -> None:
        super().__init__(executor=executor, uv_prerequisites=uv_prerequisites)
        self._source = source
        self._package = package

    @property
    def name(self) -> CommandName:
        return CommandName.PYTEST

    def run(
        self, *, check: Check, project_root: Path, output: ProcessOutput | None
    ) -> CheckResult:
        source = self._source.read()
        with TemporaryDirectory(prefix="checksmith-pytest-") as directory:
            report_path = Path(directory).resolve() / "summary.json"
            argv = self._package.build_argv(
                package=check.package,
                command="python",
                arguments=("-c", source, str(report_path), *check.arguments),
            )
            completed = self._execute_argv(
                check=check, project_root=project_root, argv=argv, output=output
            )
            return self.process_response(
                check_id=check.id,
                exit_code=completed.returncode,
                stdout=completed.stdout,
                stderr=completed.stderr,
                report_path=report_path,
            )

    def process_response(
        self,
        *,
        check_id: str,
        report_path: Path,
        exit_code: int,
        stdout: str,
        stderr: str,
    ) -> CheckResult:
        diagnostics = tuple(
            output.strip() for output in (stdout, stderr) if output.strip()
        )
        # The standalone launcher reserves 10..16 for completed pytest calls.
        # uv failures and Python import failures keep their original exit codes.
        match exit_code:
            case 10:
                status = CheckStatus.PASSED
            case 15:
                status = CheckStatus.PASSED
            case 11 | 16:
                status = CheckStatus.FAILED
            case _:
                match exit_code:
                    case 12:
                        summary = (
                            "pytest could not complete collection or was interrupted"
                        )
                    case 13:
                        summary = "pytest encountered an internal error"
                    case 14:
                        summary = "pytest rejected its configuration or arguments"
                    case _:
                        summary = (
                            f"pytest launcher exited {exit_code} without returning "
                            "a recognized pytest result"
                        )
                raise CheckOutputError(
                    check_id=check_id,
                    command=self.name,
                    summary=summary,
                    problem="\n".join(diagnostics),
                )
        try:
            summary = PytestSummary.model_validate_json(
                report_path.read_text(encoding="utf-8")
            )
        except (OSError, UnicodeDecodeError, ValidationError) as error:
            raise CheckOutputError(
                check_id=check_id,
                command=self.name,
                summary="pytest did not return a valid summary report",
                problem=str(error),
            ) from error
        messages = summary.messages()
        if exit_code == 15:
            messages += ("No tests were collected; the check passes.",)
        return CheckResult(check_id=check_id, status=status, messages=messages)
