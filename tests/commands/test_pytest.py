import subprocess
from collections.abc import Callable
from os import stat_result
from pathlib import Path

import pytest

from checksmith.commands.pytest import PytestCommand
from checksmith.config import Check, ConfigPath
from checksmith.dtos import CheckResult, CheckStatus, CommandName, PackageType
from checksmith.errors import (
    CheckExecutionError,
    CheckOutputError,
    CheckPrerequisiteError,
)
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
    exit_code: int,
    expected: CheckStatus,
    tmp_path: Path,
) -> None:
    result = PytestCommand().process_response(
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


def test_no_collected_tests_pass_with_an_explicit_explanation(tmp_path: Path) -> None:
    result = PytestCommand().process_response(
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
    exit_code: int,
    tmp_path: Path,
) -> None:
    with pytest.raises(CheckOutputError) as raised:
        PytestCommand().process_response(
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
    exit_code: int,
    tmp_path: Path,
) -> None:
    result = PytestCommand().process_response(
        check_id="quiet-tests",
        project_root=tmp_path,
        exit_code=exit_code,
        stdout=" \n",
        stderr="\n",
    )

    assert result.messages == ()


def test_pytest_uses_the_uv_project_interpreter_and_closed_stdin(
    configured_check: Check,
    project_root: Path,
    processes: FakeProcesses,
) -> None:
    processes.exit_code = 10

    result = PytestCommand().run(check=configured_check, project_root=project_root)

    process = processes.started[0]
    assert process.argv[:5] == ("uv", "run", "--locked", "python", "-c")
    assert "import pytest" in process.argv[5]
    assert process.argv[6:] == ("tests",)
    assert process.cwd == project_root
    assert process.stdin == subprocess.DEVNULL
    assert result.check_id == "unit-tests"
    assert result.status is CheckStatus.PASSED


def test_custom_arguments_and_config_paths_reach_pytest_unchanged(
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

    PytestCommand().run(check=check, project_root=project_root)

    assert processes.started[0].argv[6:] == (
        "integration tests/test_app.py::test_example",
        "-c",
        str(project_root / ".checksmith" / "pytest.ini"),
    )


@pytest.mark.parametrize("filename", ["pyproject.toml", "uv.lock"])
@pytest.mark.parametrize("kind", ["absent", "directory", "broken-symlink"])
def test_uv_requires_project_files_before_starting_even_without_tests(
    filename: str,
    kind: str,
    project_root: Path,
    configured_check: Check,
    processes: FakeProcesses,
) -> None:
    path = project_root / filename
    path.unlink()
    if kind == "directory":
        path.mkdir()
    elif kind == "broken-symlink":
        path.symlink_to(project_root / "missing")

    with pytest.raises(CheckPrerequisiteError) as raised:
        PytestCommand().run(check=configured_check, project_root=project_root)

    assert raised.value.config_file == path
    assert "uv execution requires" in str(raised.value)
    assert processes.started == []


def test_uv_does_not_search_parent_directories_for_project_files(
    project_root: Path,
    configured_check: Check,
    processes: FakeProcesses,
) -> None:
    nested = project_root / "nested"
    nested.mkdir()

    with pytest.raises(CheckPrerequisiteError) as raised:
        PytestCommand().run(check=configured_check, project_root=nested)

    assert raised.value.config_file == nested / "pyproject.toml"
    assert processes.started == []


def test_an_unreadable_project_file_is_an_error(
    project_root: Path,
    configured_check: Check,
    processes: FakeProcesses,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_stat = Path.stat
    path = project_root / "uv.lock"

    def unreadable_stat(self: Path) -> stat_result:
        if self == path:
            raise PermissionError("permission denied")
        return original_stat(self)

    monkeypatch.setattr(Path, "stat", unreadable_stat)

    with pytest.raises(CheckPrerequisiteError, match="permission denied"):
        PytestCommand().run(check=configured_check, project_root=project_root)

    assert processes.started == []


def test_an_unavailable_uv_reports_an_execution_error(
    project_root: Path,
    configured_check: Check,
    processes: FakeProcesses,
) -> None:
    processes.refusal = FileNotFoundError("uv not installed")

    with pytest.raises(CheckExecutionError) as raised:
        PytestCommand().run(check=configured_check, project_root=project_root)

    assert raised.value.program == "uv"
    assert raised.value.working_directory == project_root


@pytest.mark.parametrize("variable", ["UV_PROJECT", "UV_WORKING_DIR"])
def test_uv_cannot_redirect_the_configured_project_root(
    variable: str,
    project_root: Path,
    configured_check: Check,
    processes: FakeProcesses,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(variable, str(project_root / "another-project"))

    with pytest.raises(CheckPrerequisiteError, match=f"Unset {variable}"):
        PytestCommand().run(check=configured_check, project_root=project_root)

    assert processes.started == []


@pytest.mark.parametrize("setting", ["1", "true", "TRUE", "yes", "invalid"])
def test_uv_cannot_disable_lock_validation_through_the_environment(
    setting: str,
    project_root: Path,
    configured_check: Check,
    processes: FakeProcesses,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("UV_NO_SYNC", setting)

    with pytest.raises(
        CheckPrerequisiteError, match="UV_NO_SYNC must be unset or false"
    ):
        PytestCommand().run(check=configured_check, project_root=project_root)

    assert processes.started == []


@pytest.mark.parametrize("setting", ["0", "false", "FALSE", "no", "off"])
def test_uv_allows_sync_to_remain_enabled_explicitly(
    setting: str,
    project_root: Path,
    configured_check: Check,
    processes: FakeProcesses,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("UV_NO_SYNC", setting)
    processes.exit_code = 10

    result = PytestCommand().run(check=configured_check, project_root=project_root)

    assert result.status is CheckStatus.PASSED
    assert len(processes.started) == 1


def test_uv_requires_a_managed_project(
    project_root: Path,
    configured_check: Check,
    processes: FakeProcesses,
) -> None:
    (project_root / "pyproject.toml").write_text(
        "[tool.uv]\nmanaged = false\n", encoding="utf-8"
    )

    with pytest.raises(CheckPrerequisiteError, match="tool.uv.managed=true"):
        PytestCommand().run(check=configured_check, project_root=project_root)

    assert processes.started == []


@pytest.mark.parametrize("contents", [b"[project\n", b"\xff"])
def test_uv_reports_an_invalid_project_manifest_before_execution(
    contents: bytes,
    project_root: Path,
    configured_check: Check,
    processes: FakeProcesses,
) -> None:
    (project_root / "pyproject.toml").write_bytes(contents)

    with pytest.raises(CheckPrerequisiteError, match="invalid pyproject.toml"):
        PytestCommand().run(check=configured_check, project_root=project_root)

    assert processes.started == []


def test_prepare_does_not_require_a_uv_project(
    tmp_path: Path,
    configured_check: Check,
    processes: FakeProcesses,
) -> None:
    result = PytestCommand().prepare(check=configured_check, project_root=tmp_path)

    assert result.status is CheckStatus.SKIPPED
    assert processes.started == []
