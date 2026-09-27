import logging
import shlex
from collections.abc import Callable
from pathlib import Path

import pytest

from checksmith.commands.pytest import PackagedLauncherSource, PytestCommand
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


@pytest.mark.parametrize(
    ("exit_code", "expected"),
    [(10, CheckStatus.PASSED), (11, CheckStatus.FAILED), (16, CheckStatus.FAILED)],
)
def test_pytest_outcomes_preserve_native_diagnostics(
    command_factory: CommandFactory,
    exit_code: int,
    expected: CheckStatus,
    tmp_path: Path,
) -> None:
    result = command_factory.for_name(name=CommandName.PYTEST).process_response(
        check_id="unit-tests",
        project_root=tmp_path,
        exit_code=exit_code,
        stdout="  tests/test_app.py\n    assert actual == expected\n",
        stderr="  warning: example warning\n",
    )

    assert result == CheckResult(
        check_id="unit-tests",
        status=expected,
        messages=(
            "tests/test_app.py\n    assert actual == expected",
            "warning: example warning",
        ),
    )


def test_no_collected_tests_pass_with_an_explicit_explanation(
    command_factory: CommandFactory, tmp_path: Path
) -> None:
    result = command_factory.for_name(name=CommandName.PYTEST).process_response(
        check_id="unit-tests",
        project_root=tmp_path,
        exit_code=15,
        stdout="no tests ran in 0.01s\n",
        stderr="",
    )

    assert result.status is CheckStatus.PASSED
    assert result.messages == (
        "no tests ran in 0.01s",
        "No tests were collected; the check passes.",
    )


@pytest.mark.parametrize("exit_code", [0, 1, 2, 5, 6, 12, 13, 14, 17, 127, -9])
def test_launcher_and_pytest_errors_keep_their_diagnostics(
    command_factory: CommandFactory,
    exit_code: int,
    tmp_path: Path,
) -> None:
    with pytest.raises(CheckOutputError) as raised:
        command_factory.for_name(name=CommandName.PYTEST).process_response(
            check_id="unit-tests",
            project_root=tmp_path,
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
    tmp_path: Path,
) -> None:
    result = command_factory.for_name(name=CommandName.PYTEST).process_response(
        check_id="quiet-tests",
        project_root=tmp_path,
        exit_code=exit_code,
        stdout=" \n",
        stderr="\n",
    )

    assert result.messages == ()


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
        check=configured_check, project_root=project_root
    )

    process = processes.started[0]
    assert process.argv[:5] == ("uv", "run", "--locked", "python", "-c")
    assert "import pytest" in process.argv[5]
    assert process.argv[6:] == ("tests",)
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
        check=check, project_root=project_root
    )

    assert processes.started[0].argv[6:] == (
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
            check=configured_check, project_root=project_root
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

    command.run(check=configured_check, project_root=tmp_path)

    assert source.reads == 1
    assert package.calls == [(None, "python", ("-c", "injected launcher", "tests"))]
    assert prerequisites.calls == [(configured_check, tmp_path)]
    assert processes.started[0].argv == (
        "custom-uv",
        "run",
        "python",
        "-c",
        "injected launcher",
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
        command.run(check=configured_check, project_root=tmp_path)

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
        command.run(check=configured_check, project_root=tmp_path)

    assert prerequisites.calls == []
    assert package.calls == []
    assert processes.started == []
