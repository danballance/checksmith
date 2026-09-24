import json
from pathlib import Path
from stat import S_ISLNK
from typing import Annotated, Final, Literal, NoReturn

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

from checksmith.commands.command import Command
from checksmith.config import Check
from checksmith.dtos import CheckResult, CheckStatus, CommandName
from checksmith.errors import CheckExecutionError, CheckOutputError

REPORT_FILENAME: Final = "dependency-graph.json"
VALUE_OPTIONS: Final = frozenset(
    {
        "--output",
        "--output-dir",
        "--exclude",
        "--view",
        "--package-depth",
        "--implied-edges",
        "--project-root",
        "--expect-package",
        "--forbid",
    }
)
FLAG_OPTIONS: Final = frozenset(
    {"--include-tests", "--exclude-type-only", "--exclude-local"}
)
GATE_OPTIONS: Final = frozenset(
    {"--check", "--baseline", "--cleanup-baseline", "--allow-inventory-change"}
)


class PyArchGraphCleanup(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    violation_count: Annotated[int, Field(strict=True, ge=0)]
    possible_violation_count: Annotated[int, Field(strict=True, ge=0)]


class PyArchGraphAnalysis(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    # Upstream owns these extensible analysis settings. Preserve every key so
    # changes to settings or the analyser cannot silently weaken the comparison.
    provenance: dict[str, JsonValue] = Field(min_length=1)


class PyArchGraphReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    schema_version: Literal["0.4"]
    cleanup: PyArchGraphCleanup
    analysis: PyArchGraphAnalysis


class PyArchGraphViolationCounts(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    cyclic_dependency: Annotated[int, Field(strict=True, ge=0)]
    forbidden_dependency: Annotated[int, Field(strict=True, ge=0)]


class PyArchGraphCoverage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore", strict=True)

    complete: bool
    scope_valid: bool
    dependency_resolution_complete: bool
    nonempty: bool

    @property
    def problems(self) -> tuple[str, ...]:
        conditions = (
            ("complete", self.complete, "analysis is incomplete"),
            ("scope_valid", self.scope_valid, "source scope is invalid"),
            (
                "dependency_resolution_complete",
                self.dependency_resolution_complete,
                "dependency resolution is incomplete",
            ),
            ("nonempty", self.nonempty, "no modules were analysed"),
        )
        return tuple(
            f"{name}=false ({reason})"
            for name, satisfied, reason in conditions
            if not satisfied
        )


class PyArchGraphHealthCleanup(PyArchGraphCleanup):
    counts: PyArchGraphViolationCounts
    coverage: PyArchGraphCoverage


class PyArchGraphHealthMetrics(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    module_count: Annotated[int, Field(strict=True, ge=0)]
    dependency_count: Annotated[int, Field(strict=True, ge=0)]
    cyclic_component_count: Annotated[int, Field(strict=True, ge=0)]
    cyclic_module_count: Annotated[int, Field(strict=True, ge=0)]


class PyArchGraphHealthQuality(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    metrics: PyArchGraphHealthMetrics


class PyArchGraphHealthReport(PyArchGraphReport):
    cleanup: PyArchGraphHealthCleanup
    quality: PyArchGraphHealthQuality


class _PyArchGraphIncompleteAnalysis(CheckOutputError):
    pass


class PyArchGraphPaths(BaseModel):
    model_config = ConfigDict(frozen=True)

    baseline_report: Path
    current_report: Path
    preparation_args: tuple[str, ...]
    check_args: tuple[str, ...]


class PyArchGraphCommand(Command):
    @property
    def name(self) -> CommandName:
        return CommandName.PYARCHGRAPH

    def prepare(self, *, check: Check, project_root: Path) -> CheckResult:
        paths = self._paths(check=check, project_root=project_root)
        self._remove_report(check=check, path=paths.baseline_report)
        preparation = check.model_copy(update={"args": paths.preparation_args})
        try:
            super().run(check=preparation, project_root=project_root)
            report = self._read_report(
                check=check,
                path=paths.baseline_report,
                report_type=PyArchGraphReport,
            )
        except (CheckExecutionError, CheckOutputError):
            self._remove_report(check=check, path=paths.baseline_report)
            raise
        return CheckResult(
            check_id=check.id,
            status=CheckStatus.PASSED,
            messages=(
                (
                    f"PyArchGraph baseline saved to {paths.baseline_report}: "
                    f"cleanup.violation_count={report.cleanup.violation_count}, "
                    "cleanup.possible_violation_count="
                    f"{report.cleanup.possible_violation_count}."
                ),
            ),
        )

    def run(self, *, check: Check, project_root: Path) -> CheckResult:
        paths = self._paths(check=check, project_root=project_root)
        if not self._has_baseline(check=check, path=paths.baseline_report):
            return self._run_standalone(
                check=check, project_root=project_root, paths=paths
            )
        baseline = self._read_report(
            check=check, path=paths.baseline_report, report_type=PyArchGraphReport
        )
        self._remove_report(check=check, path=paths.current_report)
        checking = check.model_copy(update={"args": paths.check_args})
        super().run(check=checking, project_root=project_root)
        current = self._read_report(
            check=check, path=paths.current_report, report_type=PyArchGraphReport
        )
        if json.dumps(baseline.analysis.provenance, sort_keys=True) != json.dumps(
            current.analysis.provenance, sort_keys=True
        ):
            raise CheckOutputError(
                check_id=check.id,
                command=self.name,
                summary="PyArchGraph reports have incompatible analysis settings",
                problem=(
                    "analysis.provenance differs between "
                    f"{paths.baseline_report} and {paths.current_report}. "
                    "Use the same analyser and analysis settings as preparation."
                ),
            )
        before = baseline.cleanup
        after = current.cleanup
        declined = (
            after.violation_count > before.violation_count
            or after.possible_violation_count > before.possible_violation_count
        )
        messages = (
            (
                "cleanup.violation_count: "
                f"baseline={before.violation_count}, current={after.violation_count}; "
                "cleanup.possible_violation_count: "
                f"baseline={before.possible_violation_count}, "
                f"current={after.possible_violation_count}."
            ),
            f"Baseline: {paths.baseline_report}; current: {paths.current_report}.",
        )
        if declined:
            messages += (
                (
                    "Investigate cleanup.violations, cleanup.work_items, and "
                    "cleanup.coverage in the current JSON report. Fix the issues "
                    "and rerun checksmith check until both counts are at or below "
                    "their original baseline values. Keep the original baseline; "
                    "do not rerun checksmith prepare during this fix cycle."
                ),
            )
        return CheckResult(
            check_id=check.id,
            status=CheckStatus.FAILED if declined else CheckStatus.PASSED,
            messages=messages,
        )

    def _has_baseline(self, *, check: Check, path: Path) -> bool:
        try:
            # Inspect ancestors too: a dangling directory symlink is an error,
            # not an absent baseline that can select standalone checking.
            for candidate in (*reversed(path.parents), path):
                try:
                    metadata = candidate.lstat()
                except FileNotFoundError:
                    return False
                if S_ISLNK(metadata.st_mode):
                    candidate.stat()
        except OSError as error:
            raise CheckOutputError(
                check_id=check.id,
                command=self.name,
                summary=f"Cannot inspect PyArchGraph baseline {path}",
                problem=str(error),
            ) from error
        return True

    def _run_standalone(
        self, *, check: Check, project_root: Path, paths: PyArchGraphPaths
    ) -> CheckResult:
        self._remove_report(check=check, path=paths.current_report)
        checking = check.model_copy(update={"args": paths.check_args})
        try:
            super().run(check=checking, project_root=project_root)
        except _PyArchGraphIncompleteAnalysis:
            current = self._read_report(
                check=check,
                path=paths.current_report,
                report_type=PyArchGraphHealthReport,
            )
            if current.cleanup.coverage.complete:
                raise
        else:
            current = self._read_report(
                check=check,
                path=paths.current_report,
                report_type=PyArchGraphHealthReport,
            )
        cleanup = current.cleanup
        metrics = current.quality.metrics
        problems = cleanup.coverage.problems
        failed = (
            cleanup.violation_count > 0
            or cleanup.possible_violation_count > 0
            or bool(problems)
        )
        coverage = "incomplete; " + "; ".join(problems) if problems else "complete"
        messages = (
            "Standalone dependency health check; no baseline comparison was performed.",
            (
                f"Modules: {metrics.module_count}; "
                f"dependencies: {metrics.dependency_count}; "
                f"cyclic components: {metrics.cyclic_component_count}; "
                f"cyclic modules: {metrics.cyclic_module_count}."
            ),
            (
                f"cleanup.violation_count={cleanup.violation_count} "
                f"(cyclic dependencies={cleanup.counts.cyclic_dependency}, "
                f"forbidden dependencies={cleanup.counts.forbidden_dependency}); "
                f"cleanup.possible_violation_count={cleanup.possible_violation_count}."
            ),
            f"Coverage: {coverage}.",
            f"Current report: {paths.current_report}.",
        )
        if failed:
            messages += (
                (
                    "Investigate cleanup.violations, cleanup.work_items, and "
                    "cleanup.coverage in the current JSON report. Resolve all "
                    "confirmed and possible violations and coverage issues, "
                    "then rerun checksmith check."
                ),
            )
        return CheckResult(
            check_id=check.id,
            status=CheckStatus.FAILED if failed else CheckStatus.PASSED,
            messages=messages,
        )

    def process_response(
        self,
        *,
        check_id: str,
        project_root: Path,
        exit_code: int,
        stdout: str,
        stderr: str,
    ) -> CheckResult:
        if exit_code != 0:
            error_type = (
                _PyArchGraphIncompleteAnalysis if exit_code == 1 else CheckOutputError
            )
            raise error_type(
                check_id=check_id,
                command=self.name,
                summary=f"pyarchgraph exited {exit_code} instead of completing analysis",
                problem="\n".join(part.strip() for part in (stderr, stdout) if part),
            )
        return CheckResult(
            check_id=check_id,
            status=CheckStatus.PASSED,
            messages=(),
        )

    def _paths(self, *, check: Check, project_root: Path) -> PyArchGraphPaths:
        arguments = tuple(
            argument if isinstance(argument, str) else str(argument.config_path)
            for argument in check.args
        )
        outputs: list[str] = []
        directories: list[tuple[int, bool, str]] = []
        index = 0
        while index < len(arguments):
            argument = arguments[index]
            option, separator, inline_value = argument.partition("=")
            if option in GATE_OPTIONS:
                self._argument_error(
                    check=check,
                    problem=f"Do not pass {option}; Checksmith compares the counters.",
                )
            if option == "--" or option == "--json-only":
                self._argument_error(
                    check=check,
                    problem=f"{option} is unsupported; use explicit --output json.",
                )
            if option in VALUE_OPTIONS:
                option_index = index
                if separator:
                    value = inline_value
                else:
                    index += 1
                    if index == len(arguments):
                        self._argument_error(
                            check=check, problem=f"{option} requires a value."
                        )
                    value = arguments[index]
                if not value:
                    self._argument_error(
                        check=check, problem=f"{option} requires a nonempty value."
                    )
                if option == "--output":
                    outputs.append(value)
                elif option == "--output-dir":
                    directories.append((option_index, bool(separator), value))
            elif argument.startswith("-") and argument not in FLAG_OPTIONS:
                self._argument_error(
                    check=check,
                    problem=f"Unsupported PyArchGraph option {argument!r}.",
                )
            index += 1
        if outputs != ["json"] or len(directories) != 1:
            self._argument_error(
                check=check,
                problem="Pass exactly one --output json and one --output-dir PATH.",
            )
        output_index, inline, output_directory = directories[0]
        output_root = (project_root / output_directory).resolve()
        baseline_report = output_root / "baseline" / REPORT_FILENAME
        current_report = output_root / "current" / REPORT_FILENAME
        if baseline_report.resolve() == current_report.resolve():
            self._argument_error(
                check=check,
                problem=(
                    "The baseline and current directories under --output-dir "
                    "must name different reports."
                ),
            )
        preparation_args = list(arguments)
        check_args = list(arguments)
        if inline:
            preparation_args[output_index] = (
                f"--output-dir={baseline_report.parent}"
            )
            check_args[output_index] = f"--output-dir={current_report.parent}"
        else:
            preparation_args[output_index + 1] = str(baseline_report.parent)
            check_args[output_index + 1] = str(current_report.parent)
        return PyArchGraphPaths(
            baseline_report=baseline_report,
            current_report=current_report,
            preparation_args=tuple(preparation_args),
            check_args=tuple(check_args),
        )

    def _argument_error(self, *, check: Check, problem: str) -> NoReturn:
        raise CheckOutputError(
            check_id=check.id,
            command=self.name,
            summary="Invalid PyArchGraph arguments",
            problem=problem,
        )

    def _read_report[Report: PyArchGraphReport](
        self, *, check: Check, path: Path, report_type: type[Report]
    ) -> Report:
        try:
            return report_type.model_validate_json(path.read_bytes())
        except (OSError, ValidationError) as error:
            raise CheckOutputError(
                check_id=check.id,
                command=self.name,
                summary=f"Cannot read PyArchGraph report {path}",
                problem=(
                    f"{error}\nExpected a valid schema 0.4 dependency-graph.json "
                    "report. Each run must write a fresh report."
                ),
            ) from error

    def _remove_report(self, *, check: Check, path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError as error:
            raise CheckOutputError(
                check_id=check.id,
                command=self.name,
                summary=f"Cannot remove previous PyArchGraph report {path}",
                problem=str(error),
            ) from error
