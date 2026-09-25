"""Tests for :mod:`checksmith.cli`."""

import json
import subprocess
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest
import typer
import yaml
from typer.core import TyperGroup, TyperOption
from typer.testing import CliRunner

from checksmith import __version__
from checksmith.cli import (
    ChecksmithGroup,
    DebugOption,
    OutputFormat,
    _emit,
    app,
)
from checksmith.commands.command import Command
from checksmith.commands.registry import CommandFactory
from checksmith.config import Check
from checksmith.dtos import (
    CheckResult,
    CheckStatus,
    CommandName,
    ExitCode,
    PackageType,
)
from checksmith.errors import CheckOutputError, ConfigSyntaxError
from checksmith.logs import configure_logging
from checksmith.outputs.checkoutput import CheckOutput
from tests.commands.test_pyarchgraph import standalone_report_json, write_report
from tests.conftest import FakeProcesses


def test_cli_imports_the_command_factory_and_implementations(
    imported_modules: Callable[[str], frozenset[str]],
) -> None:
    modules = imported_modules("checksmith.cli")

    assert {
        "checksmith.commands.registry",
        "checksmith.commands.command",
        "checksmith.commands.import_linter",
        "checksmith.commands.pyarchgraph",
        "checksmith.commands.pytest",
        "checksmith.commands.ruff",
        "checksmith.commands.semgrep",
        "checksmith.commands.ty",
    } <= modules


@pytest.fixture
def cli_runner() -> CliRunner:
    return CliRunner()


def test_version_reports_the_installed_version(cli_runner: CliRunner) -> None:
    result = cli_runner.invoke(app, ["--version"])

    assert result.exit_code == ExitCode.SUCCESS
    assert result.stdout.strip() == f"checksmith {__version__}"


@pytest.mark.parametrize("help_option", ["-h", "--help"])
def test_both_help_options_are_accepted(
    cli_runner: CliRunner,
    help_option: str,
) -> None:
    result = cli_runner.invoke(app, [help_option])

    assert result.exit_code == ExitCode.SUCCESS
    assert "Manage coding-agent integrations" in result.stdout


def test_no_arguments_shows_help(cli_runner: CliRunner) -> None:
    result = cli_runner.invoke(app, [])

    assert "Usage" in result.stdout


@pytest.mark.parametrize("operation", ["check", "prepare"])
def test_check_requires_a_config_to_be_named(
    cli_runner: CliRunner,
    operation: str,
) -> None:
    """Click owns this one. The error boundary re-raises rather than relabels it."""
    result = cli_runner.invoke(app, [operation])

    assert result.exit_code == ExitCode.ERROR
    assert "Missing option" in result.stderr
    assert "--config" in result.stderr


def test_check_loads_the_named_config_and_runs_what_it_declares(
    cli_runner: CliRunner,
    config_tree: Path,
    processes: FakeProcesses,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Configure, execute, read, report --- the whole of a run, from the outside.

    The working directory the tool was started in is the resolved project root,
    which is that resolution proved from the command line in.
    """
    monkeypatch.chdir(config_tree)
    processes.stdout = "[]"

    result = cli_runner.invoke(
        app,
        ["check", "--config", ".checksmith/checksmith.yaml"],
    )

    assert processes.started[0].cwd == config_tree
    assert result.exit_code == ExitCode.SUCCESS
    assert "ruff" in result.stdout
    assert "PASS" in result.stdout


@pytest.fixture
def selection_config_file(config_file: Path) -> Path:
    config_file.write_text(
        config_file.read_text(encoding="utf-8").replace("id: ruff", "id: lint-src")
        + "\n"
        "  - id: lint-tests\n"
        "    package_type: uvx\n"
        '    package: "ruff==0.16.7"\n'
        "    command: ruff\n"
        "    args:\n"
        "      - check\n"
        "      - --config\n"
        "      - config_path: ruff.toml\n"
        "      - --output-format\n"
        "      - json\n"
        "      - tests\n",
        encoding="utf-8",
    )
    return config_file


@pytest.mark.parametrize("check_id", [None, "lint-tests"])
def test_selection_preserves_arguments_and_project_root(
    cli_runner: CliRunner,
    selection_config_file: Path,
    processes: FakeProcesses,
    monkeypatch: pytest.MonkeyPatch,
    check_id: str | None,
) -> None:
    monkeypatch.chdir(selection_config_file.parent)
    processes.stdout = "[]"
    arguments = ["check", "--config", "checksmith.yaml", "--format", "json"]
    if check_id is not None:
        arguments.extend(["--check", check_id])

    result = cli_runner.invoke(app, arguments)

    assert result.exit_code == ExitCode.SUCCESS
    assert result.stderr == ""
    expected_ids = ["lint-src", "lint-tests"] if check_id is None else ["lint-tests"]
    expected_targets = (".", "tests") if check_id is None else ("tests",)
    assert json.loads(result.stdout) == {
        "results": [
            {"check_id": expected_id, "status": "passed", "messages": []}
            for expected_id in expected_ids
        ]
    }
    assert [process.argv for process in processes.started] == [
        (
            "uvx",
            "--from",
            "ruff==0.16.7",
            "ruff",
            "check",
            "--config",
            str(selection_config_file.parent / "ruff.toml"),
            "--output-format",
            "json",
            target,
        )
        for target in expected_targets
    ]
    assert all(
        process.cwd == selection_config_file.parent.parent
        for process in processes.started
    )


@pytest.mark.parametrize("operation", ["check", "prepare"])
@pytest.mark.parametrize("fmt", [OutputFormat.TEXT, OutputFormat.JSON])
@pytest.mark.parametrize(
    ("status", "expected_exit_code", "label"),
    [
        (CheckStatus.PASSED, ExitCode.SUCCESS, "PASS"),
        (CheckStatus.FAILED, ExitCode.UNHEALTHY, "FAIL"),
        (CheckStatus.ERROR, ExitCode.ERROR, "ERROR"),
        (CheckStatus.SKIPPED, ExitCode.SUCCESS, "SKIP"),
    ],
)
def test_only_the_selected_check_runs_its_hooks_and_reports_a_result(
    cli_runner: CliRunner,
    selection_config_file: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    fmt: OutputFormat,
    status: CheckStatus,
    expected_exit_code: ExitCode,
    label: str,
) -> None:
    eligibility_checks: list[str] = []
    executed: list[str] = []
    skipped = operation == "check" and status is CheckStatus.SKIPPED
    expected = CheckResult(
        check_id="lint-tests",
        status=status,
        messages=("Check is not applicable to this project.",) if skipped else (),
    )

    def is_runnable(command: Command, *, check: Check, project_root: Path) -> bool:
        eligibility_checks.append(check.id)
        return status is not CheckStatus.SKIPPED

    def run(command: Command, *, check: Check, project_root: Path) -> CheckResult:
        executed.append(check.id)
        assert check.id == "lint-tests"
        assert project_root == selection_config_file.parent.parent
        assert check.arguments == (
            "check",
            "--config",
            str(selection_config_file.parent / "ruff.toml"),
            "--output-format",
            "json",
            "tests",
        )
        return expected

    monkeypatch.setattr(Command, "check_is_runnable", is_runnable)
    monkeypatch.setattr(Command, "prepare" if operation == "prepare" else "run", run)

    result = cli_runner.invoke(
        app,
        [
            operation,
            "--config",
            str(selection_config_file),
            "--check",
            "lint-tests",
            "--format",
            fmt.value,
        ],
    )

    assert result.exit_code == expected_exit_code
    assert result.stderr == ""
    assert eligibility_checks == (["lint-tests"] if operation == "check" else [])
    assert executed == ([] if skipped else ["lint-tests"])
    if fmt is OutputFormat.JSON:
        assert json.loads(result.stdout) == {
            "results": [expected.model_dump(mode="json")]
        }
    else:
        assert "lint-tests" in result.stdout
        assert "lint-src" not in result.stdout
        assert label in result.stdout


@pytest.fixture
def forbid_command_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    def registry() -> Mapping[CommandName, Command]:
        pytest.fail("Invalid input must fail before constructing commands")

    monkeypatch.setattr(CommandFactory, "registry", registry)


@pytest.mark.parametrize("operation", ["check", "prepare"])
@pytest.mark.parametrize("fmt", [OutputFormat.TEXT, OutputFormat.JSON])
@pytest.mark.parametrize(
    "check_id", ["unknown", "", "LINT-TESTS", "lint", "lint-*", "ruff"]
)
def test_an_unknown_check_id_fails_before_execution(
    cli_runner: CliRunner,
    selection_config_file: Path,
    forbid_command_registry: None,
    operation: str,
    fmt: OutputFormat,
    check_id: str,
) -> None:
    result = cli_runner.invoke(
        app,
        [
            operation,
            "--config",
            str(selection_config_file),
            "--check",
            check_id,
            "--format",
            fmt.value,
        ],
    )

    assert result.exit_code == ExitCode.ERROR
    assert result.stdout == ""
    assert result.stderr == (
        f"checksmith error: Unknown check ID {check_id!r}. "
        "Available check IDs: 'lint-src', 'lint-tests'.\n"
    )


@pytest.mark.parametrize("operation", ["check", "prepare"])
@pytest.mark.parametrize("check_id", [None, "lint-tests"])
@pytest.mark.parametrize(
    ("original", "replacement", "diagnostic"),
    [
        ("id: lint-src", "id: lint-tests", "duplicate check ID 'lint-tests'"),
        ("id: lint-src", 'id: ""', "checks.0.id"),
    ],
)
def test_selection_still_validates_the_complete_configuration(
    cli_runner: CliRunner,
    selection_config_file: Path,
    forbid_command_registry: None,
    operation: str,
    check_id: str | None,
    original: str,
    replacement: str,
    diagnostic: str,
) -> None:
    selection_config_file.write_text(
        selection_config_file.read_text(encoding="utf-8").replace(original, replacement),
        encoding="utf-8",
    )
    arguments = [
        operation, "--config", str(selection_config_file), "--format", "json"
    ]
    if check_id is not None:
        arguments.extend(["--check", check_id])

    result = cli_runner.invoke(app, arguments)

    assert result.exit_code == ExitCode.ERROR
    assert result.stdout == ""
    assert result.stderr.startswith("checksmith error:")
    assert diagnostic in result.stderr


def test_a_check_that_found_something_reports_it_and_exits_unhealthy(
    cli_runner: CliRunner,
    config_tree: Path,
    processes: FakeProcesses,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The point of the whole program: what a tool found, under the exit code for it."""
    monkeypatch.chdir(config_tree)
    processes.exit_code = 1
    processes.stdout = json.dumps(
        [
            {
                "code": "F401",
                "filename": str(config_tree / "src" / "app.py"),
                "location": {"column": 8, "row": 1},
                "message": "`os` imported but unused",
            }
        ]
    )

    result = cli_runner.invoke(
        app,
        ["check", "--config", ".checksmith/checksmith.yaml"],
    )

    assert result.exit_code == ExitCode.UNHEALTHY
    assert "FAIL" in result.stdout
    assert "src/app.py:1:8" in result.stdout


def test_check_accepts_an_absolute_config_file(
    cli_runner: CliRunner,
    config_file: Path,
    config_tree: Path,
    tmp_path: Path,
    processes: FakeProcesses,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A relative path resolves from here; an absolute one ignores here."""
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    processes.stdout = "[]"

    result = cli_runner.invoke(app, ["check", "--config", str(config_file)])

    assert processes.started[0].cwd == config_tree
    assert result.exit_code == ExitCode.SUCCESS


def test_a_tool_that_cannot_scan_is_reported_as_an_error_row(
    cli_runner: CliRunner,
    config_tree: Path,
    processes: FakeProcesses,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(config_tree)
    processes.exit_code = 2
    processes.stderr = "ruff failed\n  Cause: unknown field `nonsense_key`\n"

    result = cli_runner.invoke(
        app,
        ["check", "--config", ".checksmith/checksmith.yaml"],
    )

    assert result.exit_code == ExitCode.ERROR
    assert result.stderr == ""
    assert "ERROR" in result.stdout
    assert "Check 'ruff': ruff exited 2" in result.stdout
    assert "unknown field `nonsense_key`" in result.stdout


@pytest.fixture
def pytest_config_file(tmp_path: Path) -> Path:
    directory = tmp_path / ".checksmith"
    directory.mkdir()
    path = directory / "checksmith.yaml"
    path.write_text(
        "schema_version: 1\n"
        "project_root: ..\n"
        "checks:\n"
        "  - id: unit-tests\n"
        "    package_type: uv\n"
        "    package: null\n"
        "    command: pytest\n"
        "    args: [tests]\n",
        encoding="utf-8",
    )
    return path


@pytest.fixture
def pytest_project_root(pytest_config_file: Path) -> Path:
    root = pytest_config_file.parent.parent
    (root / "pyproject.toml").write_text(
        '[project]\nname = "sample"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    (root / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    return root


@pytest.mark.parametrize("fmt", [OutputFormat.TEXT, OutputFormat.JSON])
@pytest.mark.parametrize(
    ("tool_exit_code", "stdout", "expected_status", "expected_exit_code"),
    [
        (10, "1 passed in 0.01s\n", CheckStatus.PASSED, ExitCode.SUCCESS),
        (
            11,
            "FAILED tests/test_app.py::test_app - AssertionError\n",
            CheckStatus.FAILED,
            ExitCode.UNHEALTHY,
        ),
        (15, "no tests ran in 0.01s\n", CheckStatus.PASSED, ExitCode.SUCCESS),
        (
            16,
            "Maximum allowed warnings exceeded\n",
            CheckStatus.FAILED,
            ExitCode.UNHEALTHY,
        ),
        (1, "Failed to synchronize the environment\n", CheckStatus.ERROR,
         ExitCode.ERROR),
        (12, "ImportError while collecting tests\n", CheckStatus.ERROR,
         ExitCode.ERROR),
    ],
)
def test_cli_reports_project_pytest_results_and_native_diagnostics(
    cli_runner: CliRunner,
    pytest_config_file: Path,
    pytest_project_root: Path,
    processes: FakeProcesses,
    fmt: OutputFormat,
    tool_exit_code: int,
    stdout: str,
    expected_status: CheckStatus,
    expected_exit_code: ExitCode,
) -> None:
    processes.exit_code = tool_exit_code
    processes.stdout = stdout
    processes.stderr = "Additional pytest diagnostic\n"

    result = cli_runner.invoke(
        app,
        ["check", "--config", str(pytest_config_file), "--format", fmt.value],
    )

    assert result.exit_code == expected_exit_code
    assert result.stderr == ""
    assert len(processes.started) == 1
    assert processes.started[0].cwd == pytest_project_root
    assert processes.started[0].argv[:5] == (
        "uv", "run", "--locked", "python", "-c"
    )
    assert processes.started[0].argv[-1] == "tests"
    if fmt is OutputFormat.JSON:
        results = json.loads(result.stdout)["results"]
        assert len(results) == 1
        assert results[0]["check_id"] == "unit-tests"
        assert results[0]["status"] == expected_status.value
        output = "\n".join(results[0]["messages"])
    else:
        label = {
            CheckStatus.PASSED: "PASS",
            CheckStatus.FAILED: "FAIL",
            CheckStatus.ERROR: "ERROR",
        }[expected_status]
        assert label in result.stdout
        assert "unit-tests" in result.stdout
        output = result.stdout
    assert stdout.strip() in output
    assert processes.stderr.strip() in output


def test_cli_runs_pytest_from_the_configured_root_when_invoked_elsewhere(
    cli_runner: CliRunner,
    pytest_config_file: Path,
    pytest_project_root: Path,
    processes: FakeProcesses,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    elsewhere = pytest_project_root / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    processes.exit_code = 10

    result = cli_runner.invoke(app, ["check", "--config", str(pytest_config_file)])

    assert result.exit_code == ExitCode.SUCCESS
    assert processes.started[0].cwd == pytest_project_root


@pytest.mark.parametrize("fmt", [OutputFormat.TEXT, OutputFormat.JSON])
def test_cli_reports_a_missing_uv_project_as_an_error_even_without_tests(
    cli_runner: CliRunner,
    pytest_config_file: Path,
    processes: FakeProcesses,
    fmt: OutputFormat,
) -> None:
    result = cli_runner.invoke(
        app,
        ["check", "--config", str(pytest_config_file), "--format", fmt.value],
    )

    assert result.exit_code == ExitCode.ERROR
    assert result.stderr == ""
    assert processes.started == []
    if fmt is OutputFormat.JSON:
        results = json.loads(result.stdout)["results"]
        assert results[0]["status"] == "error"
        assert "pyproject.toml" in "\n".join(results[0]["messages"])
    else:
        assert "ERROR" in result.stdout
        assert "pyproject.toml" in result.stdout


@pytest.mark.parametrize("fmt", [OutputFormat.TEXT, OutputFormat.JSON])
def test_prepare_skips_pytest_without_loading_its_project_prerequisites(
    cli_runner: CliRunner,
    pytest_config_file: Path,
    processes: FakeProcesses,
    fmt: OutputFormat,
) -> None:
    result = cli_runner.invoke(
        app,
        ["prepare", "--config", str(pytest_config_file), "--format", fmt.value],
    )

    assert result.exit_code == ExitCode.SUCCESS
    assert result.stderr == ""
    assert processes.started == []
    if fmt is OutputFormat.JSON:
        results = json.loads(result.stdout)["results"]
        assert results[0]["check_id"] == "unit-tests"
        assert results[0]["status"] == "skipped"
        assert results[0]["messages"]
    else:
        assert "unit-tests" in result.stdout
        assert "SKIP" in result.stdout


@pytest.fixture
def semgrep_config_file(tmp_path: Path) -> Path:
    path = tmp_path / "checksmith.yaml"
    path.write_text(
        "schema_version: 1\n"
        "project_root: .\n"
        "checks:\n"
        "  - id: function-style\n"
        "    package_type: uvx\n"
        "    package: semgrep==1.176.1\n"
        "    command: semgrep\n"
        "    args: [scan, --json, --config, .checksmith/semgrep.yaml, .]\n",
        encoding="utf-8",
    )
    return path


def test_cli_reports_a_clean_semgrep_scan(
    cli_runner: CliRunner,
    semgrep_config_file: Path,
    processes: FakeProcesses,
) -> None:
    processes.stdout = '{"results": [], "errors": []}'

    result = cli_runner.invoke(
        app,
        ["check", "--config", str(semgrep_config_file), "--format", "json"],
    )

    assert result.exit_code == ExitCode.SUCCESS
    assert result.stderr == ""
    assert processes.started[0].cwd == semgrep_config_file.parent
    assert json.loads(result.stdout) == {
        "results": [{"check_id": "function-style", "status": "passed", "messages": []}]
    }


@pytest.mark.parametrize("tool_exit_code", [0, 1])
def test_cli_reports_semgrep_findings_as_an_unhealthy_check(
    cli_runner: CliRunner,
    semgrep_config_file: Path,
    processes: FakeProcesses,
    tool_exit_code: int,
) -> None:
    processes.exit_code = tool_exit_code
    processes.stdout = json.dumps(
        {
            "results": [
                {
                    "check_id": "python-no-variadic-parameters",
                    "path": "app.py",
                    "start": {"line": 1, "col": 1},
                    "extra": {"message": "Replace variadic parameters."},
                }
            ],
            "errors": [],
        }
    )

    result = cli_runner.invoke(
        app,
        ["check", "--config", str(semgrep_config_file), "--format", "json"],
    )

    assert result.exit_code == ExitCode.UNHEALTHY
    assert result.stderr == ""
    assert json.loads(result.stdout) == {
        "results": [
            {
                "check_id": "function-style",
                "status": "failed",
                "messages": [
                    (
                        "app.py:1:1 python-no-variadic-parameters "
                        "Replace variadic parameters."
                    )
                ],
            }
        ]
    }


@pytest.mark.parametrize(
    ("tool_exit_code", "stdout", "stderr"),
    [
        (2, "", "Could not load semgrep.yaml"),
        (
            0,
            '{"results": [], "errors": [{"message": "Could not load semgrep.yaml"}]}',
            "",
        ),
    ],
)
def test_cli_reports_semgrep_scan_failures_as_errors(
    cli_runner: CliRunner,
    semgrep_config_file: Path,
    processes: FakeProcesses,
    tool_exit_code: int,
    stdout: str,
    stderr: str,
) -> None:
    processes.exit_code = tool_exit_code
    processes.stdout = stdout
    processes.stderr = stderr

    result = cli_runner.invoke(
        app,
        ["check", "--config", str(semgrep_config_file)],
    )

    assert result.exit_code == ExitCode.ERROR
    assert result.stderr == ""
    assert "ERROR" in result.stdout
    assert "Check 'function-style':" in result.stdout
    assert "Could not load semgrep.yaml" in result.stdout


@pytest.fixture
def pyarchgraph_config_file(tmp_path: Path) -> Path:
    path = tmp_path / "checksmith.yaml"
    path.write_text(
        "schema_version: 1\n"
        "project_root: .\n"
        "checks:\n"
        "  - id: architecture\n"
        "    package_type: uvx\n"
        "    package: pyarchgraph==0.4.0\n"
        "    command: pyarchgraph\n"
        "    args: [src, --project-root, ., --output, json, --output-dir, build]\n",
        encoding="utf-8",
    )
    return path


def stub_pyarchgraph_process(
    *,
    monkeypatch: pytest.MonkeyPatch,
    processes: FakeProcesses,
    content: str | None,
) -> None:
    def run(
        argv: Sequence[str],
        *,
        cwd: str,
        stdin: int,
        capture_output: bool,
        text: bool,
        encoding: str,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        output_dir = Path(argv[argv.index("--output-dir") + 1])
        if content is not None:
            write_report(path=output_dir / "dependency-graph.json", content=content)
        return processes.run(
            argv,
            cwd=cwd,
            stdin=stdin,
            capture_output=capture_output,
            text=text,
            encoding=encoding,
            check=check,
        )

    monkeypatch.setattr(subprocess, "run", run)


@pytest.mark.parametrize("output_format", ["text", "json"])
@pytest.mark.parametrize(
    ("known", "possible", "complete", "tool_exit_code", "exit_code", "status"),
    [
        (0, 0, True, 0, ExitCode.SUCCESS, CheckStatus.PASSED),
        (2, 0, True, 0, ExitCode.UNHEALTHY, CheckStatus.FAILED),
        (0, 1, True, 0, ExitCode.UNHEALTHY, CheckStatus.FAILED),
        (0, 0, False, 0, ExitCode.UNHEALTHY, CheckStatus.FAILED),
        (0, 0, False, 1, ExitCode.UNHEALTHY, CheckStatus.FAILED),
    ],
)
def test_cli_reports_standalone_pyarchgraph_health(
    cli_runner: CliRunner,
    pyarchgraph_config_file: Path,
    processes: FakeProcesses,
    monkeypatch: pytest.MonkeyPatch,
    output_format: str,
    known: int,
    possible: int,
    complete: bool,
    tool_exit_code: int,
    exit_code: ExitCode,
    status: CheckStatus,
) -> None:
    content = standalone_report_json(
        known=known,
        possible=possible,
        complete=complete,
        scope_valid=True,
        dependency_resolution_complete=True,
        nonempty=True,
    )
    processes.exit_code = tool_exit_code
    stub_pyarchgraph_process(
        monkeypatch=monkeypatch, processes=processes, content=content
    )

    result = cli_runner.invoke(
        app,
        [
            "check",
            "--config",
            str(pyarchgraph_config_file),
            "--format",
            output_format,
        ],
    )

    assert result.exit_code == exit_code, result.stdout
    assert result.stderr == ""
    root = pyarchgraph_config_file.parent
    current = root / "build/current/dependency-graph.json"
    assert current.read_text(encoding="utf-8") == content
    assert not (root / "build/baseline/dependency-graph.json").exists()
    assert processes.started[0].cwd == root
    assert processes.started[0].argv == (
        "uvx",
        "--from",
        "pyarchgraph==0.4.0",
        "pyarchgraph",
        "src",
        "--project-root",
        ".",
        "--output",
        "json",
        "--output-dir",
        str(current.parent),
    )
    if output_format == "json":
        payload = json.loads(result.stdout)
        assert set(payload) == {"results"}
        assert len(payload["results"]) == 1
        assert set(payload["results"][0]) == {"check_id", "status", "messages"}
        output = CheckOutput.model_validate_json(result.stdout)
        assert output.results[0].check_id == "architecture"
        assert output.results[0].status is status
        messages = "\n".join(output.results[0].messages)
        assert "baseline" in messages.lower()
        assert "standalone" in messages.lower()
        assert str(current) in messages
        assert "coverage" in messages.lower()
    else:
        assert "architecture" in result.stdout
        assert ("PASS" if status is CheckStatus.PASSED else "FAIL") in result.stdout
        assert "baseline" in result.stdout.lower()


@pytest.mark.parametrize("output_format", ["text", "json"])
@pytest.mark.parametrize(
    ("content", "tool_exit_code", "stderr", "message"),
    [
        ("not json", 0, "", "Cannot read PyArchGraph report"),
        (None, 2, "Cannot analyze src", "pyarchgraph exited 2"),
    ],
)
def test_cli_reports_standalone_pyarchgraph_errors(
    cli_runner: CliRunner,
    pyarchgraph_config_file: Path,
    processes: FakeProcesses,
    monkeypatch: pytest.MonkeyPatch,
    output_format: str,
    content: str | None,
    tool_exit_code: int,
    stderr: str,
    message: str,
) -> None:
    processes.exit_code = tool_exit_code
    processes.stderr = stderr
    stub_pyarchgraph_process(
        monkeypatch=monkeypatch, processes=processes, content=content
    )

    result = cli_runner.invoke(
        app,
        [
            "check",
            "--config",
            str(pyarchgraph_config_file),
            "--format",
            output_format,
        ],
    )

    assert result.exit_code == ExitCode.ERROR
    assert result.stderr == ""
    assert not (
        pyarchgraph_config_file.parent / "build/baseline/dependency-graph.json"
    ).exists()
    if output_format == "json":
        output = CheckOutput.model_validate_json(result.stdout)
        assert len(output.results) == 1
        assert output.results[0].check_id == "architecture"
        assert output.results[0].status is CheckStatus.ERROR
        messages = "\n".join(output.results[0].messages)
        assert message in messages
        if stderr:
            assert stderr in messages
    else:
        assert "architecture" in result.stdout
        assert "ERROR" in result.stdout
        assert "PyArchGraph" in result.stdout or "pyarchgraph" in result.stdout


@pytest.fixture
def ty_config_file(tmp_path: Path) -> Path:
    path = tmp_path / "checksmith.yaml"
    path.write_text(
        "schema_version: 1\n"
        "project_root: .\n"
        "checks:\n"
        "  - id: types\n"
        "    package_type: uvx\n"
        "    package: ty==0.0.80\n"
        "    command: ty\n"
        "    args: [check, --output-format, gitlab, --error-on-warning, .]\n",
        encoding="utf-8",
    )
    return path


def test_cli_reports_a_clean_ty_check(
    cli_runner: CliRunner,
    ty_config_file: Path,
    processes: FakeProcesses,
) -> None:
    processes.stdout = "[]"

    result = cli_runner.invoke(
        app,
        ["check", "--config", str(ty_config_file), "--format", "json"],
    )

    assert result.exit_code == ExitCode.SUCCESS
    assert result.stderr == ""
    assert processes.started[0].cwd == ty_config_file.parent
    assert processes.started[0].argv == (
        "uvx",
        "--from",
        "ty==0.0.80",
        "ty",
        "check",
        "--output-format",
        "gitlab",
        "--error-on-warning",
        ".",
    )
    assert json.loads(result.stdout) == {
        "results": [{"check_id": "types", "status": "passed", "messages": []}]
    }


@pytest.mark.parametrize(
    ("tool_exit_code", "severity", "description"),
    [
        (1, "critical", "invalid-assignment: Object of type `str` is not assignable"),
        (0, "minor", "possibly-missing-attribute: Attribute may be missing"),
        (1, "minor", "possibly-missing-attribute: Attribute may be missing"),
    ],
)
def test_cli_reports_ty_errors_and_warnings_as_unhealthy(
    cli_runner: CliRunner,
    ty_config_file: Path,
    processes: FakeProcesses,
    tool_exit_code: int,
    severity: str,
    description: str,
) -> None:
    processes.exit_code = tool_exit_code
    processes.stdout = json.dumps(
        [
            {
                "description": description,
                "severity": severity,
                "location": {
                    "path": str(ty_config_file.parent / "src" / "app.py"),
                    "positions": {"begin": {"line": 3, "column": 5}},
                },
            }
        ]
    )

    result = cli_runner.invoke(
        app,
        ["check", "--config", str(ty_config_file), "--format", "json"],
    )

    assert result.exit_code == ExitCode.UNHEALTHY
    assert result.stderr == ""
    assert json.loads(result.stdout) == {
        "results": [
            {
                "check_id": "types",
                "status": "failed",
                "messages": [f"src/app.py:3:5 {description}"],
            }
        ]
    }


@pytest.mark.parametrize("stdout", ["not json", '[{"description": "Missing location"}]'])
def test_cli_reports_malformed_ty_output_as_an_error(
    cli_runner: CliRunner,
    ty_config_file: Path,
    processes: FakeProcesses,
    stdout: str,
) -> None:
    processes.stdout = stdout

    result = cli_runner.invoke(
        app,
        ["check", "--config", str(ty_config_file), "--format", "json"],
    )

    assert result.exit_code == ExitCode.ERROR
    assert result.stderr == ""
    results = json.loads(result.stdout)["results"]
    assert len(results) == 1
    assert results[0]["check_id"] == "types"
    assert results[0]["status"] == "error"
    assert "--output-format gitlab" in results[0]["messages"][0]


def test_cli_preserves_ty_execution_failure_output(
    cli_runner: CliRunner,
    ty_config_file: Path,
    processes: FakeProcesses,
) -> None:
    processes.exit_code = 2
    processes.stdout = "Diagnostic details from stdout"
    processes.stderr = "Could not read the project configuration"

    result = cli_runner.invoke(
        app,
        ["check", "--config", str(ty_config_file), "--format", "json"],
    )

    assert result.exit_code == ExitCode.ERROR
    assert result.stderr == ""
    results = json.loads(result.stdout)["results"]
    assert len(results) == 1
    assert results[0]["check_id"] == "types"
    assert results[0]["status"] == "error"
    message = results[0]["messages"][0]
    assert "Check 'types': ty exited 2" in message
    assert "Diagnostic details from stdout" in message
    assert "Could not read the project configuration" in message


@pytest.fixture
def import_linter_config_file(tmp_path: Path) -> Path:
    path = tmp_path / "checksmith.yaml"
    path.write_text(
        "schema_version: 1\n"
        "project_root: .\n"
        "checks:\n"
        "  - id: dependencies\n"
        "    package_type: uvx\n"
        "    package: import-linter==2.15\n"
        "    command: import-linter\n"
        "    args: [lint, --config, pyproject.toml, --no-logo]\n",
        encoding="utf-8",
    )
    return path


@pytest.fixture
def import_linter_pyproject_file(import_linter_config_file: Path) -> Path:
    path = import_linter_config_file.parent / "pyproject.toml"
    path.write_text(
        "[tool.importlinter]\n"
        'root_package = "sample"\n'
        "[[tool.importlinter.contracts]]\n"
        'name = "Domain does not import infrastructure"\n'
        'type = "forbidden"\n'
        'source_modules = ["sample.domain"]\n'
        'forbidden_modules = ["sample.infrastructure"]\n',
        encoding="utf-8",
    )
    return path


@pytest.mark.parametrize("fmt", [OutputFormat.TEXT, OutputFormat.JSON])
@pytest.mark.parametrize("pyproject", [None, "[tool.importlinter]\ncontracts = []\n"])
def test_cli_skips_import_linter_without_configured_contracts(
    cli_runner: CliRunner,
    import_linter_config_file: Path,
    processes: FakeProcesses,
    fmt: OutputFormat,
    pyproject: str | None,
) -> None:
    if pyproject is not None:
        (import_linter_config_file.parent / "pyproject.toml").write_text(
            pyproject,
            encoding="utf-8",
        )

    result = cli_runner.invoke(
        app,
        ["check", "--config", str(import_linter_config_file), "--format", fmt.value],
    )

    assert result.exit_code == ExitCode.SUCCESS
    assert result.stderr == ""
    assert processes.started == []
    if fmt is OutputFormat.JSON:
        results = json.loads(result.stdout)["results"]
        assert len(results) == 1
        assert results[0]["check_id"] == "dependencies"
        assert results[0]["status"] == "skipped"
        assert results[0]["messages"]
    else:
        assert "dependencies" in result.stdout
        assert "SKIP" in result.stdout


@pytest.mark.parametrize(
    ("tool_exit_code", "stdout", "expected_status", "expected_exit_code"),
    [
        (
            0,
            (
                "Domain does not import infrastructure KEPT\n"
                "Contracts: 1 kept, 0 broken.\n"
            ),
            CheckStatus.PASSED,
            ExitCode.SUCCESS,
        ),
        (
            1,
            (
                "Domain does not import infrastructure BROKEN\n"
                "Contracts: 0 kept, 1 broken.\n"
                "sample.domain is not allowed to import sample.infrastructure\n"
            ),
            CheckStatus.FAILED,
            ExitCode.UNHEALTHY,
        ),
    ],
)
def test_cli_runs_import_linter_with_contracts_and_reports_its_findings(
    cli_runner: CliRunner,
    import_linter_config_file: Path,
    import_linter_pyproject_file: Path,
    processes: FakeProcesses,
    tool_exit_code: int,
    stdout: str,
    expected_status: CheckStatus,
    expected_exit_code: ExitCode,
) -> None:
    processes.exit_code = tool_exit_code
    processes.stdout = stdout
    processes.stderr = "Warning: an external dependency was ignored.\n"

    result = cli_runner.invoke(
        app,
        ["check", "--config", str(import_linter_config_file), "--format", "json"],
    )

    assert result.exit_code == expected_exit_code
    assert result.stderr == ""
    assert len(processes.started) == 1
    assert processes.started[0].cwd == import_linter_pyproject_file.parent
    assert processes.started[0].argv == (
        "uvx",
        "--from",
        "import-linter==2.15",
        "import-linter",
        "lint",
        "--config",
        "pyproject.toml",
        "--no-logo",
    )
    results = json.loads(result.stdout)["results"]
    assert len(results) == 1
    assert results[0]["check_id"] == "dependencies"
    assert results[0]["status"] == expected_status.value
    messages = "\n".join(results[0]["messages"])
    assert stdout.strip() in messages
    assert processes.stderr.strip() in messages


@pytest.mark.parametrize("fmt", [OutputFormat.TEXT, OutputFormat.JSON])
def test_cli_reports_malformed_import_linter_toml_as_an_error(
    cli_runner: CliRunner,
    import_linter_config_file: Path,
    processes: FakeProcesses,
    fmt: OutputFormat,
) -> None:
    (import_linter_config_file.parent / "pyproject.toml").write_text(
        "[tool.importlinter\n",
        encoding="utf-8",
    )

    result = cli_runner.invoke(
        app,
        ["check", "--config", str(import_linter_config_file), "--format", fmt.value],
    )

    assert result.exit_code == ExitCode.ERROR
    assert result.stderr == ""
    assert processes.started == []
    if fmt is OutputFormat.JSON:
        results = json.loads(result.stdout)["results"]
        assert len(results) == 1
        assert results[0]["check_id"] == "dependencies"
        assert results[0]["status"] == "error"
        assert "pyproject.toml" in "\n".join(results[0]["messages"])
    else:
        assert "ERROR" in result.stdout
        assert "dependencies" in result.stdout
        assert "Expected ']'" in result.stdout


@pytest.mark.parametrize("fmt", [OutputFormat.TEXT, OutputFormat.JSON])
def test_prepare_skips_normal_checks_without_loading_their_prerequisites(
    cli_runner: CliRunner,
    import_linter_config_file: Path,
    processes: FakeProcesses,
    fmt: OutputFormat,
) -> None:
    (import_linter_config_file.parent / "pyproject.toml").write_text(
        "[tool.importlinter\n",
        encoding="utf-8",
    )

    result = cli_runner.invoke(
        app,
        ["prepare", "--config", str(import_linter_config_file), "--format", fmt.value],
    )

    assert result.exit_code == ExitCode.SUCCESS
    assert result.stderr == ""
    assert processes.started == []
    if fmt is OutputFormat.JSON:
        results = json.loads(result.stdout)["results"]
        assert len(results) == 1
        assert results[0]["check_id"] == "dependencies"
        assert results[0]["status"] == "skipped"
        assert results[0]["messages"]
    else:
        assert "dependencies" in result.stdout
        assert "SKIP" in result.stdout


@pytest.mark.parametrize(
    ("pyproject", "first_status", "expected_exit_code"),
    [
        ("[tool.importlinter]\ncontracts = []\n", "skipped", ExitCode.SUCCESS),
        ("[tool.importlinter\n", "error", ExitCode.ERROR),
    ],
)
def test_cli_still_runs_later_checks_after_an_import_linter_prerequisite_result(
    cli_runner: CliRunner,
    import_linter_config_file: Path,
    processes: FakeProcesses,
    pyproject: str,
    first_status: str,
    expected_exit_code: ExitCode,
) -> None:
    (import_linter_config_file.parent / "pyproject.toml").write_text(
        pyproject,
        encoding="utf-8",
    )
    with import_linter_config_file.open("a", encoding="utf-8") as config:
        config.write(
            "  - id: later\n"
            "    package_type: uvx\n"
            "    package: ruff==0.16.7\n"
            "    command: ruff\n"
            "    args: [check, --output-format, json, .]\n"
        )
    processes.stdout = "[]"

    result = cli_runner.invoke(
        app,
        ["check", "--config", str(import_linter_config_file), "--format", "json"],
    )

    assert result.exit_code == expected_exit_code
    assert result.stderr == ""
    assert len(processes.started) == 1
    assert processes.started[0].argv[3] == "ruff"
    results = json.loads(result.stdout)["results"]
    assert [entry["check_id"] for entry in results] == ["dependencies", "later"]
    assert [entry["status"] for entry in results] == [first_status, "passed"]


@pytest.mark.parametrize("operation", ["check", "prepare"])
@pytest.mark.parametrize("fmt", [OutputFormat.TEXT, OutputFormat.JSON])
def test_cli_reports_every_check_after_an_individual_tool_error(
    cli_runner: CliRunner,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    fmt: OutputFormat,
) -> None:
    check_ids = ("first", "violations", "broken", "last")
    checks = tuple(
        Check(
            id=check_id,
            package_type=PackageType.UVX,
            package="ruff==0.16.7",
            command=CommandName.RUFF,
            args=("check", "--output-format", "json", "."),
        )
        for check_id in check_ids
    )
    config_file = tmp_path / "checksmith.yaml"
    config_file.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "project_root": ".",
                "checks": [check.model_dump(mode="json") for check in checks],
            }
        ),
        encoding="utf-8",
    )
    error = CheckOutputError(
        check_id="broken",
        command=CommandName.RUFF,
        summary="ruff could not scan",
        problem="invalid configuration",
    )
    outcomes: dict[str, CheckResult | CheckOutputError] = {
        "first": CheckResult(check_id="first", status=CheckStatus.PASSED, messages=()),
        "violations": CheckResult(
            check_id="violations",
            status=CheckStatus.FAILED,
            messages=("app.py:1:1 F401 Unused import", "app.py:2:1 F821 Unknown name"),
        ),
        "broken": error,
        "last": CheckResult(check_id="last", status=CheckStatus.PASSED, messages=()),
    }
    runs: list[str] = []

    def run(command: Command, *, check: Check, project_root: Path) -> CheckResult:
        assert command.name is check.command
        assert project_root == tmp_path
        runs.append(check.id)
        outcome = outcomes[check.id]
        if isinstance(outcome, CheckOutputError):
            raise outcome
        return outcome

    monkeypatch.setattr(Command, "prepare" if operation == "prepare" else "run", run)

    result = cli_runner.invoke(
        app,
        [operation, "--config", str(config_file), "--format", fmt.value],
    )

    assert result.exit_code == ExitCode.ERROR
    assert result.stderr == ""
    assert runs == list(check_ids)
    if fmt is OutputFormat.JSON:
        assert json.loads(result.stdout) == {
            "results": [
                {"check_id": "first", "status": "passed", "messages": []},
                {
                    "check_id": "violations",
                    "status": "failed",
                    "messages": [
                        "app.py:1:1 F401 Unused import",
                        "app.py:2:1 F821 Unknown name",
                    ],
                },
                {"check_id": "broken", "status": "error", "messages": [str(error)]},
                {"check_id": "last", "status": "passed", "messages": []},
            ]
        }
    else:
        assert all(label in result.stdout for label in ("PASS", "FAIL", "ERROR"))
        assert "Messages" in result.stdout
        assert "Unused import" in result.stdout
        assert "Unknown name" in result.stdout
        assert "invalid configuration" in result.stdout
        positions = tuple(result.stdout.index(check_id) for check_id in check_ids)
        assert positions == tuple(sorted(positions))


def test_an_unexpected_tool_adapter_bug_reaches_the_cli_error_boundary(
    cli_runner: CliRunner,
    config_file: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def run(command: Command, *, check: Check, project_root: Path) -> CheckResult:
        assert command.name is check.command
        assert project_root == config_file.parent.parent
        raise RuntimeError("adapter bug")

    monkeypatch.setattr(Command, "run", run)

    result = cli_runner.invoke(app, ["check", "--config", str(config_file)])

    assert result.exit_code == ExitCode.ERROR
    assert result.stdout == ""
    assert "general error: RuntimeError: adapter bug" in result.stderr


@pytest.mark.parametrize("operation", ["check", "prepare"])
def test_check_reports_a_bad_config_option_without_typers_wording(
    cli_runner: CliRunner,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    """Typer does no path validation, so the config file is opened before it complains."""
    monkeypatch.chdir(tmp_path)

    result = cli_runner.invoke(app, [operation, "--config", "absent.yaml"])

    assert result.exit_code == ExitCode.ERROR
    # The operating system's own wording, resolved to the absolute path the
    # loader actually tried.
    assert result.stderr.startswith(
        "checksmith error: [Errno 2] No such file or directory"
    )
    assert str(tmp_path / "absent.yaml") in result.stderr


def test_a_configuration_error_reaches_stderr_whatever_the_format(
    cli_runner: CliRunner,
    tmp_path: Path,
    processes: FakeProcesses,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)

    result = cli_runner.invoke(
        app,
        ["check", "--format", "json", "--config", "absent.yaml"],
    )

    assert result.exit_code == ExitCode.ERROR
    assert result.stdout == ""
    assert "No such file or directory" in result.stderr
    assert processes.started == []


def test_emit_renders_json_when_asked(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(typer.Exit):
        _emit(CheckOutput(), OutputFormat.JSON)

    assert json.loads(capsys.readouterr().out) == {"results": []}


def test_emit_renders_a_table_by_default(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(typer.Exit):
        _emit(
            CheckOutput(
                results=(
                    CheckResult(
                        check_id="ruff", status=CheckStatus.PASSED, messages=()
                    ),
                )
            ),
            OutputFormat.TEXT,
        )

    captured = capsys.readouterr().out
    assert "Checksmith" in captured
    assert "ruff" in captured


def test_emit_exits_with_the_code_its_output_implies(
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = CheckOutput(
        results=(
            CheckResult(
                check_id="ruff",
                status=CheckStatus.FAILED,
                messages=("app.py:1:1 F401 Unused import",),
            ),
        )
    )

    with pytest.raises(typer.Exit) as raised:
        _emit(output, OutputFormat.TEXT)

    assert raised.value.exit_code == ExitCode.UNHEALTHY


@pytest.mark.parametrize("operation", ["check", "prepare"])
def test_check_rejects_an_unknown_format(
    cli_runner: CliRunner,
    operation: str,
) -> None:
    result = cli_runner.invoke(app, [operation, "--format", "yaml"])

    assert result.exit_code == ExitCode.ERROR


@pytest.mark.parametrize(
    "command",
    [["agents", "install"], ["agents", "uninstall"], ["init"]],
)
def test_unimplemented_commands_fail_loudly(
    cli_runner: CliRunner,
    command: list[str],
) -> None:
    result = cli_runner.invoke(app, command)

    assert result.exit_code == ExitCode.ERROR
    assert "NotImplementedError" in result.stderr


def _grouped_app(name: str, body: Callable[[], None]) -> typer.Typer:
    """Build a throwaway app whose root group is the Checksmith error boundary.

    The callback is what makes Typer emit a group rather than collapsing a
    lone command into a bare ``Command``, which would bypass ``cls``. It carries
    the same ``--debug`` wiring as the real root callback, so what these tests
    exercise is the arrangement the real app uses.
    """
    built = typer.Typer(cls=ChecksmithGroup)

    @built.callback()
    def _root(debug: DebugOption = False) -> None:
        configure_logging(debug=debug)

    built.command(name)(body)
    return built


def test_an_unhandled_error_exits_with_the_error_code(
    cli_runner: CliRunner,
) -> None:
    def boom() -> None:
        raise RuntimeError("kaboom")

    result = cli_runner.invoke(_grouped_app("boom", boom), ["boom"])

    assert result.exit_code == ExitCode.ERROR
    assert "RuntimeError: kaboom" in result.stderr


def test_an_authored_diagnostic_is_not_labelled_with_its_class(
    cli_runner: CliRunner,
) -> None:
    """A ``ChecksmithError`` was written to be read; a prefix would spoil it."""

    def empty() -> None:
        raise ConfigSyntaxError(
            config_file=Path("/project/.checksmith/checksmith.yaml"),
            problem=(
                "expected a mapping at the top level in "
                '"/project/.checksmith/checksmith.yaml", found NoneType'
            ),
        )

    result = cli_runner.invoke(_grouped_app("empty", empty), ["empty"])

    assert result.exit_code == ExitCode.ERROR
    assert result.stderr == (
        "checksmith error: expected a mapping at the top level in "
        '"/project/.checksmith/checksmith.yaml", found NoneType\n'
    )


def test_a_deliberate_exit_passes_through_the_error_boundary(
    cli_runner: CliRunner,
) -> None:
    def unhealthy() -> None:
        raise typer.Exit(ExitCode.UNHEALTHY)

    result = cli_runner.invoke(_grouped_app("unhealthy", unhealthy), ["unhealthy"])

    assert result.exit_code == ExitCode.UNHEALTHY
    assert result.stderr == ""


def test_output_format_values_match_the_cli_schema() -> None:
    assert [fmt.value for fmt in OutputFormat] == ["text", "json"]


CLI_SCHEMA_PATH = Path(__file__).parent.parent / "checksmith-cli.yaml"


@pytest.mark.parametrize("operation", ["check", "prepare"])
def test_check_selector_matches_the_cli_schema_and_appears_in_help(
    cli_runner: CliRunner,
    operation: str,
) -> None:
    schema = yaml.safe_load(CLI_SCHEMA_PATH.read_text(encoding="utf-8"))
    command = next(
        entry for entry in schema["command"]["commands"]
        if entry["name"] == operation
    )
    declared = next(option for option in command["options"] if option["name"] == "--check")
    root = typer.main.get_command(app)
    assert isinstance(root, TyperGroup)
    option = next(
        parameter for parameter in root.commands[operation].params
        if parameter.name == "check_id"
    )

    assert isinstance(option, TyperOption)
    assert option.opts == [declared["name"]]
    assert option.required is declared["required"] is False
    assert option.nargs == len(declared["arguments"]) == 1
    assert declared["arguments"][0]["required"] is True
    result = cli_runner.invoke(app, [operation, "--help"])
    assert result.exit_code == ExitCode.SUCCESS
    assert "--check" in result.stdout


def _schema_command_paths(
    entries: Iterable[Mapping[str, Any]],
    prefix: tuple[str, ...],
) -> set[tuple[str, ...]]:
    """Collect the invocable command paths the OpenCLI schema declares.

    A schema entry carrying ``commands`` is a group, so it contributes its
    members rather than itself --- there is nothing to invoke at the group.
    """
    paths: set[tuple[str, ...]] = set()
    for entry in entries:
        path = (*prefix, str(entry["name"]))
        children = entry.get("commands")
        if children is None:
            paths.add(path)
        else:
            paths |= _schema_command_paths(children, path)
    return paths


def _cli_command_paths(
    group: TyperGroup,
    prefix: tuple[str, ...],
) -> set[tuple[str, ...]]:
    """Collect the invocable command paths Typer actually exposes."""
    paths: set[tuple[str, ...]] = set()
    for name, command in group.commands.items():
        path = (*prefix, name)
        if isinstance(command, TyperGroup):
            paths |= _cli_command_paths(command, path)
        else:
            paths.add(path)
    return paths


def test_command_names_match_the_cli_schema() -> None:
    """The schema is the interface; drift between it and Typer is a bug."""
    schema = yaml.safe_load(CLI_SCHEMA_PATH.read_text())
    root = typer.main.get_command(app)
    assert isinstance(root, TyperGroup)

    declared = _schema_command_paths(schema["command"]["commands"], ())

    assert declared == _cli_command_paths(root, ())
    assert declared == {
        ("agents", "install"),
        ("agents", "uninstall"),
        ("check",),
        ("init",),
        ("prepare",),
    }


def test_exit_code_values_match_the_cli_schema() -> None:
    assert ExitCode.SUCCESS == 0
    assert ExitCode.UNHEALTHY == 1
    assert ExitCode.ERROR == 2


def test_exit_code_is_usable_as_an_int() -> None:
    assert int(ExitCode.UNHEALTHY) == 1
