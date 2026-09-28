import logging
import shlex
from collections.abc import Callable
from pathlib import Path

import pytest

from checksmith.commands.pytest import (
    PackagedLauncherSource,
    PytestCommand,
    PytestCoverage,
    PytestFailure,
    PytestSummary,
)
from checksmith.commands.registry import CommandFactory
from checksmith.config import Check, ConfigPath
from checksmith.dtos import CheckResult, CheckStatus, CommandName, PackageType
from checksmith.errors import (
    CheckExecutionError,
    CheckOutputError,
    CheckPrerequisiteError,
)
from checksmith.packages import UvPackage
from tests.conftest import FakeProcesses


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    (tmp_path / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    (tmp_path / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    return tmp_path


@pytest.fixture
def configured_check() -> Check:
    return Check(
        id="unit-tests",
        package_type=PackageType.UV,
        package=None,
        command=CommandName.PYTEST,
        args=("tests",),
    )


def test_the_adapter_does_not_import_the_host_pytest(
    imported_modules: Callable[[str], frozenset[str]],
) -> None:
    modules = imported_modules("checksmith.commands.pytest")

    assert "pytest" not in modules
    assert "checksmith.commands._pytest_launcher" not in modules


@pytest.fixture
def summary_report(tmp_path: Path) -> Path:
    report_path = tmp_path / "summary.json"
    report_path.write_text(
        PytestSummary(
            outcomes={"passed": 2, "failed": 1},
            duration_seconds=0.25,
            warnings=1,
            deselected=3,
            failures=(
                PytestFailure(
                    nodeid="tests/test_app.py::test_bad",
                    phase="call",
                    reason="assert 1 == 2",
                ),
            ),
            coverage=PytestCoverage(total=85.0, minimum=90.0),
        ).model_dump_json(),
        encoding="utf-8",
    )
    return report_path


@pytest.mark.parametrize(
    ("exit_code", "expected"),
    [(10, CheckStatus.PASSED), (11, CheckStatus.FAILED), (16, CheckStatus.FAILED)],
)
def test_pytest_outcomes_use_summary_without_repeating_transcripts(
    command_factory: CommandFactory,
    exit_code: int,
    expected: CheckStatus,
    summary_report: Path,
) -> None:
    result = command_factory.for_name(name=CommandName.PYTEST).process_response(
        check_id="unit-tests",
        report_path=summary_report,
        exit_code=exit_code,
        stdout="full traceback and progress",
        stderr="captured stderr",
    )
    assert result == CheckResult(
        check_id="unit-tests",
        status=expected,
        messages=(
            "1 failed, 2 passed, 1 warning, 3 deselected in 0.25s",
            "FAILED tests/test_app.py::test_bad: assert 1 == 2",
            "Total coverage: 85.00% (required: 90.00%)",
        ),
    )


def test_no_collected_tests_pass_with_an_explicit_explanation(
    command_factory: CommandFactory,
    summary_report: Path,
) -> None:
    result = command_factory.for_name(name=CommandName.PYTEST).process_response(
        check_id="unit-tests",
        report_path=summary_report,
        exit_code=15,
        stdout="no tests ran in 0.01s\n",
        stderr="",
    )
    assert result.status is CheckStatus.PASSED
    assert result.messages[-1] == "No tests were collected; the check passes."


@pytest.mark.parametrize("exit_code", [0, 1, 2, 5, 6, 12, 13, 14, 17, 127, -9])
def test_launcher_and_pytest_errors_keep_their_diagnostics(
    command_factory: CommandFactory,
    exit_code: int,
    tmp_path: Path,
) -> None:
    with pytest.raises(CheckOutputError) as raised:
        command_factory.for_name(name=CommandName.PYTEST).process_response(
            check_id="unit-tests",
            report_path=tmp_path / "missing.json",
            exit_code=exit_code,
            stdout="collection details\n",
            stderr="configuration problem\n",
        )
    assert raised.value.check_id == "unit-tests"
    assert raised.value.command is CommandName.PYTEST
    assert raised.value.problem == "collection details\nconfiguration problem"


@pytest.mark.parametrize("exit_code", [10, 11, 16])
def test_quiet_pytest_results_do_not_require_a_terminal_summary(
    command_factory: CommandFactory,
    exit_code: int,
    summary_report: Path,
) -> None:
    result = command_factory.for_name(name=CommandName.PYTEST).process_response(
        check_id="quiet-tests",
        report_path=summary_report,
        exit_code=exit_code,
        stdout=" \n",
        stderr="\n",
    )
    assert result.messages[0] == "1 failed, 2 passed, 1 warning, 3 deselected in 0.25s"


@pytest.mark.parametrize("report", [None, b"{", b"\xff", b"{}", b'{"outcomes": []}'])
def test_missing_and_malformed_reports_are_clear_errors(
    command_factory: CommandFactory,
    tmp_path: Path,
    report: bytes | None,
) -> None:
    report_path = tmp_path / "summary.json"
    if report is not None:
        report_path.write_bytes(report)
    with pytest.raises(CheckOutputError, match="valid summary report"):
        command_factory.for_name(name=CommandName.PYTEST).process_response(
            check_id="unit-tests",
            report_path=report_path,
            exit_code=10,
            stdout="1 passed",
            stderr="",
        )


def test_pytest_uses_the_uv_project_interpreter(
    command_factory: CommandFactory,
    configured_check: Check,
    project_root: Path,
    processes: FakeProcesses,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG, logger="checksmith.commands.command")
    processes.exit_code = 10

    result = command_factory.for_name(name=CommandName.PYTEST).run(
        check=configured_check, project_root=project_root, output=None
    )

    process = processes.started[0]
    assert process.argv[:5] == ("uv", "run", "--locked", "python", "-c")
    assert "import pytest" in process.argv[5]
    assert Path(process.argv[6]).is_absolute()
    assert not Path(process.argv[6]).exists()
    assert process.argv[7:] == ("tests",)
    assert process.cwd == project_root
    assert process.check_id == "unit-tests"
    assert result.check_id == "unit-tests"
    assert result.status is CheckStatus.PASSED
    message = next(
        text for text in caplog.messages if text.startswith("unit-tests command=")
    )
    rendered = message.removeprefix("unit-tests command=").removesuffix(
        f" cwd={project_root}"
    )
    assert tuple(shlex.split(rendered)) == process.argv


def test_custom_arguments_and_config_paths_reach_pytest_unchanged(
    command_factory: CommandFactory,
    project_root: Path,
    processes: FakeProcesses,
) -> None:
    processes.exit_code = 10
    config_path = ConfigPath.model_validate(
        {"config_path": "pytest.ini"},
        context=project_root / ".checksmith" / "checksmith.yaml",
    )
    check = Check(
        id="custom-tests",
        package_type=PackageType.UV,
        package=None,
        command=CommandName.PYTEST,
        args=("integration tests/test_app.py::test_example", "-c", config_path),
    )

    command_factory.for_name(name=CommandName.PYTEST).run(
        check=check, project_root=project_root, output=None
    )

    assert processes.started[0].argv[7:] == (
        "integration tests/test_app.py::test_example",
        "-c",
        str(project_root / ".checksmith" / "pytest.ini"),
    )


def test_an_unavailable_uv_reports_an_execution_error(
    command_factory: CommandFactory,
    project_root: Path,
    configured_check: Check,
    processes: FakeProcesses,
) -> None:
    processes.refusal = FileNotFoundError("uv not installed")

    with pytest.raises(CheckExecutionError) as raised:
        command_factory.for_name(name=CommandName.PYTEST).run(
            check=configured_check, project_root=project_root, output=None
        )

    assert raised.value.program == "uv"
    assert raised.value.working_directory == project_root


class RecordingSource:
    def __init__(self, content: str) -> None:
        self.content = content
        self.reads = 0

    def read(self) -> str:
        self.reads += 1
        return self.content


class RecordingPackage(UvPackage):
    def __init__(self) -> None:
        self.calls: list[tuple[str | None, str, tuple[str, ...]]] = []

    def build_argv(
        self, *, package: str | None, command: str, arguments: tuple[str, ...]
    ) -> tuple[str, ...]:
        self.calls.append((package, command, arguments))
        return ("custom-uv", "run", command, *arguments)


class RecordingPrerequisites:
    def __init__(self, failure: CheckPrerequisiteError | None) -> None:
        self.failure = failure
        self.calls: list[tuple[Check, Path]] = []

    def validate(self, *, check: Check, project_root: Path) -> None:
        self.calls.append((check, project_root))
        if self.failure is not None:
            raise self.failure


def test_pytest_uses_injected_source_package_and_prerequisites(
    configured_check: Check, processes: FakeProcesses, tmp_path: Path
) -> None:
    source = RecordingSource(content="injected launcher")
    package = RecordingPackage()
    prerequisites = RecordingPrerequisites(failure=None)
    command = PytestCommand(
        executor=processes,
        uv_prerequisites=prerequisites,
        source=source,
        package=package,
    )
    processes.exit_code = 10
    assert source.reads == 0

    command.run(check=configured_check, project_root=tmp_path, output=None)

    assert source.reads == 1
    report_path = package.calls[0][2][2]
    assert package.calls == [
        (None, "python", ("-c", "injected launcher", report_path, "tests"))
    ]
    assert prerequisites.calls == [(configured_check, tmp_path)]
    assert processes.started[0].argv == (
        "custom-uv",
        "run",
        "python",
        "-c",
        "injected launcher",
        report_path,
        "tests",
    )


def test_failed_prerequisites_prevent_process_execution(
    configured_check: Check, processes: FakeProcesses, tmp_path: Path
) -> None:
    failure = CheckPrerequisiteError(
        check_id=configured_check.id,
        command=CommandName.PYTEST,
        config_file=tmp_path / "uv.lock",
        problem="locked project missing",
    )
    command = PytestCommand(
        executor=processes,
        uv_prerequisites=RecordingPrerequisites(failure=failure),
        source=RecordingSource(content="launcher"),
        package=RecordingPackage(),
    )

    with pytest.raises(CheckPrerequisiteError) as raised:
        command.run(check=configured_check, project_root=tmp_path, output=None)

    assert raised.value is failure
    assert processes.started == []


def test_packaged_launcher_source_reads_utf8_only_when_requested(
    tmp_path: Path,
) -> None:
    resource = tmp_path / "launcher.py"
    source = PackagedLauncherSource(resource=resource)
    resource.write_text("print('café')\n", encoding="utf-8")

    assert source.read() == "print('café')\n"


def test_failed_launcher_reads_propagate_before_validation_or_execution(
    configured_check: Check, processes: FakeProcesses, tmp_path: Path
) -> None:
    prerequisites = RecordingPrerequisites(failure=None)
    package = RecordingPackage()
    command = PytestCommand(
        executor=processes,
        uv_prerequisites=prerequisites,
        source=PackagedLauncherSource(resource=tmp_path / "missing.py"),
        package=package,
    )

    with pytest.raises(FileNotFoundError):
        command.run(check=configured_check, project_root=tmp_path, output=None)

    assert prerequisites.calls == []
    assert package.calls == []
    assert processes.started == []


@pytest.mark.parametrize(
    ("outcomes", "warnings", "coverage", "expected"),
    [
        ({}, 0, None, ("No tests ran in 0.00s",)),
        (
            {"error": 2, "custom": 1},
            2,
            None,
            ("2 errors, 1 custom, 2 warnings in 0.00s",),
        ),
        (
            {"passed": 1},
            0,
            PytestCoverage(total=100.0, minimum=None),
            ("1 passed in 0.00s", "Total coverage: 100.00%"),
        ),
        (
            {},
            0,
            PytestCoverage(total=None, minimum=None),
            ("No tests ran in 0.00s", "Total coverage: not reported"),
        ),
    ],
)
def test_summary_formats_optional_counts_and_coverage(
    outcomes: dict[str, int],
    warnings: int,
    coverage: PytestCoverage | None,
    expected: tuple[str, ...],
) -> None:
    summary = PytestSummary(
        outcomes=outcomes,
        duration_seconds=0.0,
        warnings=warnings,
        deselected=0,
        failures=(),
        coverage=coverage,
    )
    assert summary.messages() == expected


def test_reusing_command_uses_separate_report_files(
    command_factory: CommandFactory,
    configured_check: Check,
    project_root: Path,
    processes: FakeProcesses,
) -> None:
    command = command_factory.for_name(name=CommandName.PYTEST)
    processes.exit_code = 10
    first = command.run(check=configured_check, project_root=project_root, output=None)
    processes.pytest_report = PytestSummary(
        outcomes={"passed": 3},
        duration_seconds=1.0,
        warnings=0,
        deselected=0,
        failures=(),
        coverage=None,
    )
    second = command.run(check=configured_check, project_root=project_root, output=None)
    assert first.messages == ("No tests ran in 0.00s",)
    assert second.messages == ("3 passed in 1.00s",)
    paths = [Path(process.argv[6]) for process in processes.started]
    assert paths[0] != paths[1]
    assert all(not path.parent.exists() for path in paths)
