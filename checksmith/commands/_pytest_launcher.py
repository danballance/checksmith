import json
import os
import stat
import sys
from io import StringIO
from pathlib import Path
from time import perf_counter
from typing import Protocol, TextIO, TypedDict, cast

import pytest

EXIT_CODE_OFFSET = 10


class FailureReport(TypedDict):
    nodeid: str
    phase: str
    reason: str


class CoverageReport(TypedDict):
    total: float | None
    minimum: float | None


class SummaryReport(TypedDict):
    outcomes: dict[str, int]
    duration_seconds: float
    warnings: int
    deselected: int
    failures: list[FailureReport]
    coverage: CoverageReport | None


class _CoverageOptions(Protocol):
    cov_fail_under: float | None
    no_cov: bool
    cov_report: dict[str, str | None] | list[str]
    no_cov_on_fail: bool


class _CoverageData(Protocol):
    def report(self, *, file: TextIO, output_format: str) -> float: ...


class _CoverageController(Protocol):
    cov: _CoverageData


class _CoveragePlugin(Protocol):
    options: _CoverageOptions
    cov_total: float | None
    cov_controller: _CoverageController | None
    failed: bool


class _ChecksmithPlugin:
    def __init__(self) -> None:
        self.collection_failed: bool = False
        self.outcomes: dict[str, int] = {}
        self.warnings = 0
        self.deselected = 0
        self.failures: list[FailureReport] = []
        self._config: pytest.Config | None = None
        self._coverage: _CoveragePlugin | None = None
        self._coverage_minimum: float | None = None

    def pytest_configure(self, config: pytest.Config) -> None:
        self._config = config

    @pytest.hookimpl(trylast=True)
    def pytest_sessionstart(self, session: pytest.Session) -> None:
        coverage = cast(
            _CoveragePlugin | None, session.config.pluginmanager.get_plugin("_cov")
        )
        if coverage is not None and not coverage.options.no_cov:
            self._coverage = coverage
            self._coverage_minimum = coverage.options.cov_fail_under

    def pytest_warning_recorded(self) -> None:
        self.warnings += 1

    def pytest_deselected(self, items: list[pytest.Item]) -> None:
        self.deselected += len(items)

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        self._record_report(report)

    def _record_report(self, report: pytest.TestReport | pytest.CollectReport) -> None:
        assert self._config is not None
        category, _, _ = self._config.hook.pytest_report_teststatus(
            report=report, config=self._config
        )
        if category:
            self.outcomes[category] = self.outcomes.get(category, 0) + 1
        if report.failed:
            longrepr = report.longrepr
            crash = getattr(longrepr, "reprcrash", None)
            reason = str(crash.message) if crash is not None else report.longreprtext
            phase = (
                "collect" if isinstance(report, pytest.CollectReport) else report.when
            )
            assert phase is not None
            self.failures.append(
                {
                    "nodeid": report.nodeid,
                    "phase": phase,
                    "reason": " ".join(reason.split())[:240],
                }
            )

    def report(self, duration_seconds: float, empty_suite: bool) -> SummaryReport:
        coverage: CoverageReport | None = None
        if self._coverage is not None:
            total = self._coverage.cov_total
            if total is None and not empty_suite:
                options = self._coverage.options
                reports_enabled = bool(options.cov_report or options.cov_fail_under)
                reports_suppressed = not reports_enabled or (
                    options.no_cov_on_fail and self._coverage.failed
                )
                if not reports_suppressed:
                    raise RuntimeError("pytest coverage did not report a total")
                controller = self._coverage.cov_controller
                if controller is None:
                    raise RuntimeError("pytest coverage did not provide collected data")
                total = controller.cov.report(file=StringIO(), output_format="total")
            coverage = {"total": total, "minimum": self._coverage_minimum}
        return {
            "outcomes": self.outcomes,
            "duration_seconds": duration_seconds,
            "warnings": self.warnings,
            "deselected": self.deselected,
            "failures": self.failures,
            "coverage": coverage,
        }

    def pytest_collectreport(self, report: pytest.CollectReport) -> None:
        if report.failed or report.skipped:
            self._record_report(report)
        if report.failed:
            self.collection_failed = True

    @pytest.hookimpl(tryfirst=True)
    def pytest_collection(self, session: pytest.Session) -> bool | None:
        config = session.config
        if config.getoption("pyargs") or len(config.args) != 1:
            return None

        tests_path = Path.cwd() / "tests"
        selected_path = Path(os.path.abspath(config.args[0]))
        if selected_path != tests_path:
            return None

        try:
            tests_path.lstat()
        except FileNotFoundError:
            return True
        except OSError as error:
            raise pytest.UsageError(
                f"Cannot access default tests directory {tests_path}: {error}"
            ) from error

        try:
            mode = tests_path.stat().st_mode
        except OSError as error:
            raise pytest.UsageError(
                f"Cannot access default tests directory {tests_path}: {error}"
            ) from error

        if not stat.S_ISDIR(mode):
            raise pytest.UsageError(
                f"Default tests target must be a directory: {tests_path}"
            )
        return None

    @pytest.hookimpl(tryfirst=True)
    def pytest_runtestloop(self, session: pytest.Session) -> None:
        if self.collection_failed or session.testsfailed or session.items:
            return
        coverage_plugin = session.config.pluginmanager.get_plugin("_cov")
        if coverage_plugin is not None:
            cast(_CoveragePlugin, coverage_plugin).options.cov_fail_under = 0.0


class PytestInvocation(Protocol):
    def __call__(self, args: list[str], plugins: list[object]) -> int: ...


class PytestLauncher:
    def __init__(self, invoke_pytest: PytestInvocation, error_stream: TextIO) -> None:
        self._invoke_pytest = invoke_pytest
        self._error_stream = error_stream

    def run(self, *, args: list[str], report_path: Path) -> int:
        if not report_path.is_absolute():
            raise ValueError("pytest summary report path must be absolute")
        plugin = _ChecksmithPlugin()
        started_at = perf_counter()
        exit_code = int(
            self._invoke_pytest(
                self._coverage_config_arguments(arguments=args), plugins=[plugin]
            )
        )
        if exit_code not in range(7):
            print(
                f"pytest returned an unexpected exit code: {exit_code}",
                file=self._error_stream,
            )
            return 2
        if plugin.collection_failed:
            return EXIT_CODE_OFFSET + pytest.ExitCode.INTERRUPTED
        if exit_code in {0, 1, 5, 6}:
            report_path.write_text(
                json.dumps(
                    plugin.report(perf_counter() - started_at, exit_code == 5),
                    allow_nan=False,
                ),
                encoding="utf-8",
            )
        return EXIT_CODE_OFFSET + exit_code

    def _coverage_config_arguments(self, *, arguments: list[str]) -> list[str]:
        normalized: list[str] = []
        index = 0
        while index < len(arguments):
            option = arguments[index]
            if option == "--":
                normalized.extend(arguments[index:])
                break
            if (
                option == "--cov-config"
                and index + 1 < len(arguments)
                and not arguments[index + 1].startswith("-")
            ):
                normalized.append(f"{option}={arguments[index + 1]}")
                index += 2
            else:
                normalized.append(option)
                index += 1
        return normalized


if __name__ == "__main__":
    sys.exit(
        PytestLauncher(invoke_pytest=pytest.main, error_stream=sys.stderr).run(
            args=sys.argv[2:], report_path=Path(sys.argv[1])
        )
    )
