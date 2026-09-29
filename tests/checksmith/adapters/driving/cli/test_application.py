"""Tests for :mod:`checksmith.adapters.driving.cli.application`."""

import json
import logging
import os
import subprocess
import sys
from collections.abc import Callable, Iterable, Mapping
from importlib.resources import files
from pathlib import Path
from typing import Any, TypedDict

import pytest
import typer
import yaml
from pydantic import JsonValue
from rich.console import Console
from rich.logging import RichHandler
from typer.core import TyperGroup, TyperOption
from typer.testing import CliRunner

from checksmith import __version__
from checksmith.adapters.driven.configuration.initialization import YamlConfigRenderer
from checksmith.adapters.driven.configuration.loading import (
    ConfigLoader,
    LocalYamlConfigSource,
)
from checksmith.adapters.driven.execution.command import Command
from checksmith.adapters.driven.execution.processes import (
    LocalProcessRuntime,
    ProcessOutput,
    SubprocessExecutor,
)
from checksmith.adapters.driven.execution.pytest import PytestFailure, PytestSummary
from checksmith.adapters.driven.execution.registry import CommandFactory
from checksmith.adapters.driven.filesystem.initialization import (
    LocalInitializationFilesystem,
    PackagedAssetSource,
)
from checksmith.adapters.driving.cli.application import (
    ChecksmithGroup,
    CliApplication,
    DebugOption,
    OutputFormat,
)
from checksmith.adapters.driving.cli.logs import LOGGER_NAME, LoggingConfigurator
from checksmith.adapters.driving.cli.presentation import (
    ConsoleProcessOutput,
    OutputPresenter,
)
from checksmith.adapters.driving.cli.setup import InitializationInput
from checksmith.application.use_cases.checking import CheckService, CommandRegistry
from checksmith.application.use_cases.initialization import Initializer
from checksmith.domain.config import Check
from checksmith.domain.errors import (
    CheckOutputError,
    ChecksmithError,
    ConfigSyntaxError,
)
from checksmith.domain.models import (
    CheckResult,
    CheckStatus,
    CommandName,
    ExitCode,
    PackageType,
)
from checksmith.domain.results import CheckOutput
from checksmith.main import app as production_app
from tests.checksmith.adapters.driven.execution.test_pyarchgraph import (
    HEALTHY_REPORT,
    custom_report_json,
    cycle_finding,
    partial_report_json,
    report_json,
)
from tests.conftest import FakeProcesses, make_command_factory


def test_composition_root_imports_the_command_factory_and_implementations(
    imported_modules: Callable[[str], frozenset[str]],
) -> None:
    modules = imported_modules("checksmith.main")

    assert {
        "checksmith.adapters.driven.execution.registry",
        "checksmith.adapters.driven.execution.command",
        "checksmith.adapters.driven.execution.complexipy",
        "checksmith.adapters.driven.execution.import_linter",
        "checksmith.adapters.driven.execution.pyarchgraph",
        "checksmith.adapters.driven.execution.pytest",
        "checksmith.adapters.driven.execution.ruff",
        "checksmith.adapters.driven.execution.semgrep",
        "checksmith.adapters.driven.execution.ty",
    } <= modules


class ConfiguredTerminal:
    def is_interactive(self) -> bool:
        return True


class PolicyPlugin(TypedDict):
    id: str
    settings: dict[str, JsonValue]


class PolicyDocument(TypedDict):
    sources: list[str]
    exclusions: list[str]
    plugins: list[PolicyPlugin]


def policy_document() -> PolicyDocument:
    return {
        "sources": ["src"],
        "exclusions": [],
        "plugins": [{"id": "oopcheck", "settings": {
            "max_standalone_percent": 25,
            "min_callable_statements": 20,
            "min_shared_functions": 3,
            "collaborators": [],
        }}],
    }


class FakeAstcheckConfigurator:
    def configure(
        self, *, package: str, project_root: Path, config_file: Path,
        policy: Path | None,
    ) -> bytes:
        return yaml.safe_dump({
            **policy_document(),
            "schema_version": 1,
            "project_root": os.path.relpath(project_root, config_file.parent),
        }).encode("utf-8")


def make_app(commands: CommandRegistry) -> typer.Typer:
    return CliApplication(
        checks=CheckService(
            loader=ConfigLoader(source=LocalYamlConfigSource()), commands=commands
        ),
        initializer=Initializer(
            assets=PackagedAssetSource(
                directory=files("checksmith") / "assets" / "default"
            ),
            renderer=YamlConfigRenderer(),
            filesystem=LocalInitializationFilesystem(),
            astcheck=FakeAstcheckConfigurator(),
        ),
        terminal=InitializationInput(terminal=ConfiguredTerminal()),
        presenter=OutputPresenter(console=Console()),
        logging=LoggingConfigurator(
            logger=logging.getLogger(LOGGER_NAME),
            handler=RichHandler(
                console=Console(stderr=True),
                show_time=True,
                show_level=True,
                show_path=True,
                rich_tracebacks=True,
                markup=False,
            ),
        ),
    ).build()


@pytest.fixture
def astcheck_policy_file(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("policies") / "policy.yaml"
    path.write_text(yaml.safe_dump(policy_document()), encoding="utf-8")
    return path


@pytest.fixture
def app(command_factory: CommandFactory) -> typer.Typer:
    return make_app(commands=command_factory)


class FixedCommandRegistry:
    def __init__(self, commands: Mapping[CommandName, Command]) -> None:
        self.commands = commands

    def registry(self) -> Mapping[CommandName, Command]:
        return self.commands


class ForbiddenCommandRegistry:
    def registry(self) -> Mapping[CommandName, Command]:
        pytest.fail("Invalid input must fail before constructing commands")


class ScriptedCommand(Command):
    def __init__(
        self, outcomes: Mapping[str, CheckResult | Exception], runnable: bool
    ) -> None:
        self.outcomes = outcomes
        self.runnable = runnable
        self.eligibility_checks: list[str] = []
        self.executed: list[Check] = []
        self.roots: list[Path] = []

    @property
    def name(self) -> CommandName:
        return CommandName.RUFF

    def check_is_runnable(self, *, check: Check, project_root: Path) -> bool:
        self.eligibility_checks.append(check.id)
        return self.runnable

    def run(
        self, *, check: Check, project_root: Path, output: ProcessOutput | None
    ) -> CheckResult:
        self.executed.append(check)
        self.roots.append(project_root)
        outcome = self.outcomes[check.id]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class PythonProcessExecutor:
    def __init__(self, source: str) -> None:
        self.source = source

    def run(
        self,
        *,
        check_id: str,
        argv: tuple[str, ...],
        cwd: Path,
        heartbeat_interval_seconds: float,
        output: ProcessOutput | None,
    ) -> subprocess.CompletedProcess[str]:
        return SubprocessExecutor(runtime=LocalProcessRuntime()).run(
            check_id=check_id,
            argv=(sys.executable, "-c", self.source),
            cwd=cwd,
            heartbeat_interval_seconds=heartbeat_interval_seconds,
            output=output,
        )


@pytest.fixture
def cli_runner() -> CliRunner:
    return CliRunner()


def test_version_reports_the_installed_version(
    app: typer.Typer, cli_runner: CliRunner
) -> None:
    result = cli_runner.invoke(app, ["--version"])

    assert result.exit_code == ExitCode.SUCCESS
    assert result.stdout.strip() == f"checksmith {__version__}"


@pytest.mark.parametrize("help_option", ["-h", "--help"])
def test_both_help_options_are_accepted(
    app: typer.Typer,
    cli_runner: CliRunner,
    help_option: str,
) -> None:
    result = cli_runner.invoke(app, [help_option])

    assert result.exit_code == ExitCode.SUCCESS
    assert "Manage coding-agent integrations" in result.stdout


def test_no_arguments_shows_help(app: typer.Typer, cli_runner: CliRunner) -> None:
    result = cli_runner.invoke(app, [])

    assert "Usage" in result.stdout


def test_init_prompts_with_the_current_directory_and_accepts_enter(
    app: typer.Typer,
    cli_runner: CliRunner,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("COLUMNS", "300")

    result = cli_runner.invoke(app, ["init"], input="\n.\n\n\n\n")

    assert result.exit_code == ExitCode.SUCCESS
    assert result.stderr == ""
    assert f"Project root [{tmp_path}]" in result.stdout
    assert {path.name for path in tmp_path.iterdir()} == {
        "checksmith.yaml",
        "ruff.toml",
        "semgrep.yaml",
        "coverage.toml",
        "astcheck.yaml",
    }
    assert all(
        filename in result.stdout
        for filename in (
            "checksmith.yaml",
            "ruff.toml",
            "semgrep.yaml",
            "coverage.toml",
            "astcheck.yaml",
        )
    )
    config_path = tmp_path / "checksmith.yaml"
    assert yaml.safe_load(config_path.read_text())["project_root"] == "."
    assert (
        ConfigLoader(source=LocalYamlConfigSource())
        .load(config_file=config_path, working_directory=tmp_path)
        .project_root
        == tmp_path
    )


@pytest.mark.parametrize("absolute", [False, True])
@pytest.mark.parametrize("prompt", [False, True])
def test_init_selects_another_project_root_and_writes_in_the_current_directory(
    astcheck_policy_file: Path,
    app: typer.Typer,
    cli_runner: CliRunner,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    absolute: bool,
    prompt: bool,
) -> None:
    destination = tmp_path / "configuration"
    destination.mkdir()
    project_root = tmp_path / "my project: 'one' #two"
    project_root.mkdir()
    monkeypatch.chdir(destination)
    selected_root = str(project_root) if absolute else f"../{project_root.name}"
    arguments = ["init"] if prompt else ["init", "--project-root", selected_root]
    arguments.extend(["--astcheck-policy", str(astcheck_policy_file)])

    result = cli_runner.invoke(
        app,
        arguments,
        input=f"{selected_root}\n" if prompt else None,
    )

    assert result.exit_code == ExitCode.SUCCESS
    assert result.stderr == ""
    assert (f"Project root [{destination}]" in result.stdout) is prompt
    config_path = destination / "checksmith.yaml"
    assert yaml.safe_load(config_path.read_text())["project_root"] == (
        f"../{project_root.name}"
    )
    config = ConfigLoader(source=LocalYamlConfigSource()).load(
        config_file=config_path, working_directory=destination
    )
    assert config.project_root == project_root
    assert list(project_root.iterdir()) == []


@pytest.mark.parametrize("root_kind", ["missing", "file"])
@pytest.mark.parametrize("prompt", [False, True])
def test_init_reports_invalid_roots_without_retrying_or_creating_files(
    astcheck_policy_file: Path,
    app: typer.Typer,
    cli_runner: CliRunner,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    root_kind: str,
    prompt: bool,
) -> None:
    project_root = tmp_path / root_kind
    if root_kind == "file":
        project_root.touch()
    before = set(tmp_path.iterdir())
    monkeypatch.chdir(tmp_path)
    arguments = ["init"] if prompt else ["init", "--project-root", str(project_root)]
    arguments.extend(["--astcheck-policy", str(astcheck_policy_file)])

    result = cli_runner.invoke(
        app,
        arguments,
        input=f"{project_root}\n" if prompt else None,
    )

    assert result.exit_code == ExitCode.ERROR
    assert result.stderr.startswith("checksmith error:")
    assert str(project_root) in result.stderr
    assert result.stdout.count(f"Project root [{tmp_path}]") == int(prompt)
    assert set(tmp_path.iterdir()) == before


def test_init_refuses_collisions_without_creating_other_files(
    astcheck_policy_file: Path,
    app: typer.Typer,
    cli_runner: CliRunner,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    existing_file = tmp_path / "ruff.toml"
    existing_file.write_text("existing configuration", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    result = cli_runner.invoke(
        app,
        ["init", "--project-root", ".", "--astcheck-policy", str(astcheck_policy_file)],
    )

    assert result.exit_code == ExitCode.ERROR
    assert result.stdout == ""
    assert result.stderr.startswith("checksmith error:")
    assert "ruff.toml" in result.stderr
    assert existing_file.read_text(encoding="utf-8") == "existing configuration"
    assert list(tmp_path.iterdir()) == [existing_file]


def test_init_aborts_on_end_of_input_without_creating_files(
    app: typer.Typer,
    cli_runner: CliRunner,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)

    result = cli_runner.invoke(app, ["init"], input="")

    assert result.exit_code == ExitCode.UNHEALTHY
    assert "Aborted" in result.stderr
    assert "checksmith error:" not in result.stderr
    assert list(tmp_path.iterdir()) == []


def test_init_aborts_on_keyboard_interrupt_without_creating_files(
    app: typer.Typer,
    cli_runner: CliRunner,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def interrupt(prompt: str) -> str:
        raise KeyboardInterrupt

    monkeypatch.chdir(tmp_path)

    with (
        cli_runner.isolation() as (_, stderr, _),
        monkeypatch.context() as prompt_patch,
    ):
        prompt_patch.setattr("click.termui.visible_prompt_func", interrupt)

        with pytest.raises(SystemExit) as raised:
            typer.main.get_command(app).main(["init"])

        assert raised.value.code == ExitCode.UNHEALTHY
        assert "Aborted" in stderr.getvalue().decode("utf-8")
        assert "checksmith error:" not in stderr.getvalue().decode("utf-8")
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    ("arguments", "missing"),
    [
        (["init"], "--project-root and --astcheck-policy"),
        (["init", "--project-root", "."], "--astcheck-policy"),
    ],
)
def test_noninteractive_init_requires_explicit_root_and_policy(
    app: typer.Typer,
    cli_runner: CliRunner,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    arguments: list[str],
    missing: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(ConfiguredTerminal, "is_interactive", lambda self: False)

    result = cli_runner.invoke(app, arguments)

    assert result.exit_code == ExitCode.ERROR
    assert result.stdout == ""
    assert missing in result.stderr
    assert list(tmp_path.iterdir()) == []


def test_noninteractive_init_reads_policy_relative_to_invocation_directory(
    app: typer.Typer,
    cli_runner: CliRunner,
    astcheck_policy_file: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "configuration"
    destination.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    (project / "src").mkdir()
    monkeypatch.chdir(destination)
    monkeypatch.setattr(ConfiguredTerminal, "is_interactive", lambda self: False)
    policy_path = Path(os.path.relpath(astcheck_policy_file, destination))

    result = cli_runner.invoke(
        app,
        [
            "init",
            "--project-root",
            "../project",
            "--astcheck-policy",
            str(policy_path),
        ],
    )

    assert result.exit_code == ExitCode.SUCCESS
    assert "Source path" not in result.stdout
    assert result.stderr == ""
    assert yaml.safe_load((destination / "astcheck.yaml").read_text()) == {
        **policy_document(),
        "schema_version": 1,
        "project_root": "../project",
    }
    assert yaml.safe_load(astcheck_policy_file.read_text()) == policy_document()


def test_invalid_policy_causes_no_initialization_writes(
    app: typer.Typer,
    cli_runner: CliRunner,
    astcheck_policy_file: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    astcheck_policy_file.write_text(
        "sources: [src]\nexclusions: []\nplugins: [{id: oopcheck, settings: {}}]",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    def refuse_configuration(
        self: FakeAstcheckConfigurator, *, package: str, project_root: Path,
        config_file: Path, policy: Path | None,
    ) -> bytes:
        raise ChecksmithError("Invalid ASTcheck plugin configuration")

    monkeypatch.setattr(FakeAstcheckConfigurator, "configure", refuse_configuration)
    result = cli_runner.invoke(
        app,
        ["init", "--project-root", ".", "--astcheck-policy", str(astcheck_policy_file)],
    )

    assert result.exit_code == ExitCode.ERROR
    assert "Invalid ASTcheck plugin configuration" in result.stderr
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("operation", ["check"])
def test_check_requires_a_config_to_be_named(
    app: typer.Typer,
    cli_runner: CliRunner,
    operation: str,
) -> None:
    """Click owns this one. The error boundary re-raises rather than relabels it."""
    result = cli_runner.invoke(app, [operation])

    assert result.exit_code == ExitCode.ERROR
    assert "Missing option" in result.stderr
    assert "--config" in result.stderr


@pytest.mark.parametrize("fmt", [OutputFormat.TEXT, OutputFormat.JSON])
@pytest.mark.parametrize(
    ("prefix", "suffix"),
    [((), ()), (("--debug",), ()), (("-d",), ()), ((), ("--debug",)), ((), ("-d",))],
)
def test_process_output_follows_text_debug_and_json_modes(
    cli_runner: CliRunner,
    config_file: Path,
    monkeypatch: pytest.MonkeyPatch,
    fmt: OutputFormat,
    prefix: tuple[str, ...],
    suffix: tuple[str, ...],
) -> None:
    source = "import sys; print('[]'); sys.stderr.write('tool progress\\n')"

    app = make_app(
        commands=make_command_factory(executor=PythonProcessExecutor(source))
    )
    monkeypatch.setenv("COLUMNS", "300")

    result = cli_runner.invoke(
        app,
        [
            *prefix,
            "check",
            "--config",
            str(config_file),
            "--format",
            fmt.value,
            *suffix,
        ],
    )

    assert result.exit_code == ExitCode.SUCCESS
    if prefix or suffix:
        for message in (
            "starting check 1/1: ruff",
            "ruff argv=",
            "ruff command=",
            "ruff started program=",
            "pid=",
            "stdout: []",
            "stderr: tool progress",
            "process exited",
            "elapsed=",
            "ruff processing output",
            "ruff status=passed",
        ):
            assert message in result.stderr
    else:
        assert result.stderr == ""
    assert "stdout: []" not in result.stdout
    live = fmt is OutputFormat.TEXT and not (prefix or suffix)
    assert ("tool progress" in result.stdout) is live
    assert ("[]" in result.stdout.splitlines()) is live
    assert "DEBUG" not in result.stdout
    if fmt is OutputFormat.JSON:
        output = CheckOutput.model_validate_json(result.stdout)
        assert output.results == (
            CheckResult(check_id="ruff", status=CheckStatus.PASSED, messages=()),
        )
    else:
        assert "PASS" in result.stdout


@pytest.mark.parametrize(
    "modes",
    [
        (("--debug",), (), ("--format", "json"), ()),
        ((), ("--format", "json", "--debug"), (), ("--debug",)),
    ],
)
def test_repeated_invocations_do_not_leak_debug_or_streaming_state(
    cli_runner: CliRunner,
    config_file: Path,
    modes: tuple[tuple[str, ...], ...],
) -> None:
    source = "import sys; print('[]'); sys.stderr.write('tool progress\\n')"
    app = make_app(
        commands=make_command_factory(executor=PythonProcessExecutor(source))
    )

    for mode in modes:
        result = cli_runner.invoke(app, ["check", "--config", str(config_file), *mode])

        assert result.exit_code == ExitCode.SUCCESS
        debug = "--debug" in mode
        structured = "json" in mode
        assert ("tool progress" in result.stdout) is (not debug and not structured)
        assert ("tool progress" in result.stderr) is debug
        if structured:
            assert CheckOutput.model_validate_json(result.stdout).exit_code == 0
        else:
            assert "PASS" in result.stdout


def test_live_output_preserves_carriage_returns_escapes_and_partial_lines(
    cli_runner: CliRunner,
    config_file: Path,
) -> None:
    source = (
        "import sys; print('[]', flush=True); "
        "sys.stderr.write('[bold]progress\\r\\x1b[31mdone\\x1b[0m')"
    )
    app = make_app(
        commands=make_command_factory(executor=PythonProcessExecutor(source))
    )

    result = cli_runner.invoke(app, ["check", "--config", str(config_file)])

    assert result.exit_code == ExitCode.SUCCESS
    assert result.stderr == ""
    assert b"[bold]progress\r\x1b[31mdone\x1b[0m" in result.stdout_bytes
    report = result.stdout_bytes.index(b"Checksmith")
    assert (
        b"\n"
        in result.stdout_bytes[
            result.stdout_bytes.index(b"\x1b[0m") + len(b"\x1b[0m") : report
        ]
    )
    assert "PASS" in result.stdout


def test_live_output_write_failure_reaches_the_cli_error_boundary(
    cli_runner: CliRunner,
    config_file: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_write(self: ConsoleProcessOutput, text: str) -> None:
        raise OSError("output stream closed")

    app = make_app(
        commands=make_command_factory(executor=PythonProcessExecutor("print('[]')"))
    )
    monkeypatch.setattr(ConsoleProcessOutput, "write", fail_write)

    result = cli_runner.invoke(app, ["check", "--config", str(config_file)])

    assert result.exit_code == ExitCode.ERROR
    assert result.stdout == ""
    assert "could not forward process output: output stream closed" in result.stderr
    assert "Checksmith" not in result.stdout


def test_check_loads_the_named_config_and_runs_what_it_declares(
    app: typer.Typer,
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
    app: typer.Typer,
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


@pytest.mark.parametrize("operation", ["check"])
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
    operation: str,
    fmt: OutputFormat,
    status: CheckStatus,
    expected_exit_code: ExitCode,
    label: str,
) -> None:
    skipped = operation == "check" and status is CheckStatus.SKIPPED
    expected = CheckResult(
        check_id="lint-tests",
        status=status,
        messages=("Check is not applicable to this project.",) if skipped else (),
    )
    command = ScriptedCommand(
        outcomes={"lint-tests": expected}, runnable=status is not CheckStatus.SKIPPED
    )
    app = make_app(commands=FixedCommandRegistry({CommandName.RUFF: command}))

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
    assert command.eligibility_checks == (
        ["lint-tests"] if operation == "check" else []
    )
    assert [check.id for check in command.executed] == (
        [] if skipped else ["lint-tests"]
    )
    if not skipped:
        assert command.roots == [selection_config_file.parent.parent]
        assert command.executed[0].arguments == (
            "check",
            "--config",
            str(selection_config_file.parent / "ruff.toml"),
            "--output-format",
            "json",
            "tests",
        )
    if fmt is OutputFormat.JSON:
        assert json.loads(result.stdout) == {
            "results": [expected.model_dump(mode="json")]
        }
    else:
        assert "lint-tests" in result.stdout
        assert "lint-src" not in result.stdout
        assert label in result.stdout


@pytest.mark.parametrize("operation", ["check"])
@pytest.mark.parametrize("fmt", [OutputFormat.TEXT, OutputFormat.JSON])
@pytest.mark.parametrize(
    "check_id", ["unknown", "", "LINT-TESTS", "lint", "lint-*", "ruff"]
)
def test_an_unknown_check_id_fails_before_execution(
    cli_runner: CliRunner,
    selection_config_file: Path,
    operation: str,
    fmt: OutputFormat,
    check_id: str,
) -> None:
    app = make_app(commands=ForbiddenCommandRegistry())

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


@pytest.mark.parametrize("operation", ["check"])
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
    operation: str,
    check_id: str | None,
    original: str,
    replacement: str,
    diagnostic: str,
) -> None:
    selection_config_file.write_text(
        selection_config_file.read_text(encoding="utf-8").replace(
            original, replacement
        ),
        encoding="utf-8",
    )
    arguments = [operation, "--config", str(selection_config_file), "--format", "json"]
    if check_id is not None:
        arguments.extend(["--check", check_id])

    app = make_app(commands=ForbiddenCommandRegistry())

    result = cli_runner.invoke(app, arguments)

    assert result.exit_code == ExitCode.ERROR
    assert result.stdout == ""
    assert result.stderr.startswith("checksmith error:")
    assert diagnostic in result.stderr


def test_a_check_that_found_something_reports_it_and_exits_unhealthy(
    app: typer.Typer,
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
    app: typer.Typer,
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
    app: typer.Typer,
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
        (
            1,
            "Failed to synchronize the environment\n",
            CheckStatus.ERROR,
            ExitCode.ERROR,
        ),
        (12, "ImportError while collecting tests\n", CheckStatus.ERROR, ExitCode.ERROR),
    ],
)
def test_cli_reports_pytest_summaries_and_preserves_error_diagnostics(
    app: typer.Typer,
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
    processes.stdout = "progress lines and captured test output\n" + stdout
    processes.stderr = "Additional pytest diagnostic\n"
    processes.pytest_report = PytestSummary(
        outcomes={"failed": 1} if tool_exit_code == 11 else {"passed": 1},
        duration_seconds=0.12,
        warnings=int(tool_exit_code == 16),
        deselected=0,
        failures=(
            PytestFailure(
                nodeid="tests/test_app.py::test_app",
                phase="call",
                reason="AssertionError",
            ),
        )
        if tool_exit_code == 11
        else (),
        coverage=None,
    )
    if tool_exit_code == 15:
        processes.pytest_report = processes.pytest_report.model_copy(
            update={"outcomes": {}}
        )

    result = cli_runner.invoke(
        app,
        ["check", "--config", str(pytest_config_file), "--format", fmt.value],
    )

    assert result.exit_code == expected_exit_code
    assert result.stderr == ""
    assert len(processes.started) == 1
    assert processes.started[0].cwd == pytest_project_root
    assert processes.started[0].argv[:5] == ("uv", "run", "--locked", "python", "-c")
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
    if expected_status is CheckStatus.ERROR:
        assert stdout.strip() in output
        assert processes.stderr.strip() in output
    else:
        assert "progress lines and captured test output" not in output
        assert processes.stderr.strip() not in output
        assert processes.pytest_report.messages()[0] in output
        if tool_exit_code == 11:
            assert "FAILED tests/test_app.py::test_app: AssertionError" in output
        if tool_exit_code == 15:
            assert "No tests were collected; the check passes." in output


def test_cli_runs_pytest_from_the_configured_root_when_invoked_elsewhere(
    app: typer.Typer,
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
    app: typer.Typer,
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
    app: typer.Typer,
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
    app: typer.Typer,
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
    app: typer.Typer,
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
        "    package: pyarchgraph @ git+https://github.com/danballance/pyarchgraph@main\n"
        "    command: pyarchgraph\n"
        "    args: [src]\n",
        encoding="utf-8",
    )
    return path


@pytest.fixture
def pyarchgraph_selection_config_file(pyarchgraph_config_file: Path) -> Path:
    pyarchgraph_config_file.write_text(
        pyarchgraph_config_file.read_text(encoding="utf-8")
        .replace("id: architecture", "id: pyarchgraph-modules")
        .replace("args: [src]", "args: [., --gate, structural]")
        + "  - id: pyarchgraph-packages\n"
        "    package_type: uvx\n"
        "    package: pyarchgraph @ git+https://github.com/danballance/pyarchgraph@main\n"
        "    command: pyarchgraph\n"
        "    args: [., --gate, package-structural]\n",
        encoding="utf-8",
    )
    return pyarchgraph_config_file


@pytest.mark.parametrize(
    "check_id", [None, "pyarchgraph-modules", "pyarchgraph-packages"]
)
def test_cli_runs_module_and_package_architecture_checks_by_id(
    app: typer.Typer,
    cli_runner: CliRunner,
    pyarchgraph_selection_config_file: Path,
    processes: FakeProcesses,
    check_id: str | None,
) -> None:
    processes.stdout = HEALTHY_REPORT
    arguments = [
        "check",
        "--config",
        str(pyarchgraph_selection_config_file),
        "--format",
        "json",
    ]
    if check_id is not None:
        arguments.extend(["--check", check_id])

    result = cli_runner.invoke(app, arguments)

    assert result.exit_code == ExitCode.SUCCESS, result.stdout
    assert result.stderr == ""
    expected_checks = {
        "pyarchgraph-modules": "structural",
        "pyarchgraph-packages": "package-structural",
    }
    if check_id is not None:
        expected_checks = {check_id: expected_checks[check_id]}
    output = CheckOutput.model_validate_json(result.stdout)
    assert [entry.check_id for entry in output.results] == list(expected_checks)
    assert all(entry.status is CheckStatus.PASSED for entry in output.results)
    assert [process.check_id for process in processes.started] == list(expected_checks)
    assert [process.argv for process in processes.started] == [
        (
            "uvx",
            "--isolated",
            "--refresh-package",
            "pyarchgraph",
            "--from",
            "pyarchgraph @ git+https://github.com/danballance/pyarchgraph@main",
            "pyarchgraph",
            ".",
            "--gate",
            gate,
        )
        for gate in expected_checks.values()
    ]
    assert all(
        process.cwd == pyarchgraph_selection_config_file.parent
        for process in processes.started
    )


@pytest.mark.parametrize("output_format", ["text", "json"])
@pytest.mark.parametrize(
    ("content", "tool_exit_code", "status", "exit_code"),
    [
        (HEALTHY_REPORT, 0, CheckStatus.PASSED, ExitCode.SUCCESS),
        (
            custom_report_json(severity="warning"),
            0,
            CheckStatus.PASSED,
            ExitCode.SUCCESS,
        ),
        (
            report_json(findings=[cycle_finding("possible")]),
            1,
            CheckStatus.FAILED,
            ExitCode.UNHEALTHY,
        ),
    ],
)
def test_cli_reports_pyarchgraph_findings(
    app: typer.Typer,
    cli_runner: CliRunner,
    pyarchgraph_config_file: Path,
    processes: FakeProcesses,
    output_format: str,
    content: str,
    tool_exit_code: int,
    status: CheckStatus,
    exit_code: ExitCode,
) -> None:
    processes.exit_code = tool_exit_code
    processes.stdout = content
    result = cli_runner.invoke(
        app,
        ["check", "--config", str(pyarchgraph_config_file), "--format", output_format],
    )

    assert result.exit_code == exit_code, result.stdout
    assert result.stderr == ""
    root = pyarchgraph_config_file.parent
    assert not (root / "build").exists()
    assert processes.started[0].cwd == root
    assert processes.started[0].argv == (
        "uvx",
        "--isolated",
        "--refresh-package",
        "pyarchgraph",
        "--from",
        "pyarchgraph @ git+https://github.com/danballance/pyarchgraph@main",
        "pyarchgraph",
        "src",
    )
    if output_format == "json":
        output = CheckOutput.model_validate_json(result.stdout)
        assert output.results[0].check_id == "architecture"
        assert output.results[0].status is status
        gate = json.loads(content)["gate"]
        assert output.results[0].messages[0].startswith(f"{gate}: ")
        assert "source files analyzed: 2/2." in output.results[0].messages[1]
        if gate == "packages":
            finding = next(
                message
                for message in output.results[0].messages
                if message.startswith("Finding ")
            )
            assert "Warning [group-size]" in finding
            assert "Application package" in finding
    else:
        assert "architecture" in result.stdout
        assert ("PASS" if status is CheckStatus.PASSED else "FAIL") in result.stdout
        if status is CheckStatus.PASSED:
            assert "No error findings." in result.stdout
            assert "Selected view:" not in result.stdout
            assert "determines pass/fail" not in result.stdout
            assert "Finding 1 of" not in result.stdout
            assert "Warning [group-size]" not in result.stdout
            assert "Application package" not in result.stdout
            assert "Other views" not in result.stdout
            assert result.stdout.rstrip().endswith("┘")
        else:
            assert "Selected view:" in result.stdout
            assert "determines pass/fail" in result.stdout
            assert result.stdout.index("Selected view:") > result.stdout.index("└")
            assert "Finding 1 of" in result.stdout
            assert result.stdout.index("Finding 1 of") < result.stdout.index("Other views")



@pytest.mark.parametrize("output_format", ["text", "json"])
@pytest.mark.parametrize(
    ("content", "tool_exit_code", "stderr", "message"),
    [
        ("not json", 0, "", "Invalid PyArchGraph report"),
        ("", 2, "Cannot analyze src", "pyarchgraph exited 2"),
        (HEALTHY_REPORT, 1, "", "exit code disagrees"),
        (
            HEALTHY_REPORT.replace('"0.8"', '"0.7"'),
            0,
            "",
            "schema 0.8",
        ),
        (
            partial_report_json(findings=[cycle_finding("definite")]),
            2,
            "",
            "source_syntax_error",
        ),
    ],
)
def test_cli_reports_pyarchgraph_errors(
    app: typer.Typer,
    cli_runner: CliRunner,
    pyarchgraph_config_file: Path,
    processes: FakeProcesses,
    output_format: str,
    content: str,
    tool_exit_code: int,
    stderr: str,
    message: str,
) -> None:
    processes.exit_code = tool_exit_code
    processes.stderr = stderr
    processes.stdout = content
    result = cli_runner.invoke(
        app,
        ["check", "--config", str(pyarchgraph_config_file), "--format", output_format],
    )

    assert result.exit_code == ExitCode.ERROR
    assert result.stderr == ""
    if output_format == "json":
        output = CheckOutput.model_validate_json(result.stdout)
        assert output.results[0].status is CheckStatus.ERROR
        messages = "\n".join(output.results[0].messages)
        assert message in messages
        assert stderr in messages
    else:
        assert "architecture" in result.stdout
        assert "ERROR" in result.stdout


def test_prepare_is_no_longer_a_command(
    app: typer.Typer, cli_runner: CliRunner
) -> None:
    result = cli_runner.invoke(app, ["prepare"])
    assert result.exit_code == ExitCode.ERROR
    assert "No such command" in result.stderr


@pytest.fixture
def complexipy_config_file(tmp_path: Path) -> Path:
    path = tmp_path / "checksmith.yaml"
    path.write_text(
        "schema_version: 1\n"
        "project_root: .\n"
        "checks:\n"
        "  - id: cognitive-complexity\n"
        "    package_type: uvx\n"
        "    package: complexipy==8.0.1\n"
        "    command: complexipy\n"
        "    args:\n"
        "      - --plain\n"
        "      - --failed\n"
        "      - --max-complexity-allowed\n"
        '      - "10"\n'
        "      - --exclude\n"
        "      - tests/**\n"
        "      - --color\n"
        '      - "no"\n'
        "      - --snapshot-ignore\n"
        "      - --snapshot-create=false\n"
        "      - --ignore-complexity=false\n"
        "      - --report-ignored=false\n"
        "      - .\n",
        encoding="utf-8",
    )
    return path


@pytest.mark.parametrize("stderr", ["", "Installed 1 package in 12ms\n"])
def test_cli_reports_a_clean_complexipy_check(
    app: typer.Typer,
    cli_runner: CliRunner,
    complexipy_config_file: Path,
    processes: FakeProcesses,
    stderr: str,
) -> None:
    processes.stdout = ""
    processes.stderr = stderr

    result = cli_runner.invoke(
        app,
        ["check", "--config", str(complexipy_config_file), "--format", "json"],
    )

    assert result.exit_code == ExitCode.SUCCESS
    assert result.stderr == ""
    assert processes.started[0].cwd == complexipy_config_file.parent
    assert processes.started[0].argv == (
        "uvx",
        "--from",
        "complexipy==8.0.1",
        "complexipy",
        "--plain",
        "--failed",
        "--max-complexity-allowed",
        "10",
        "--exclude",
        "tests/**",
        "--color",
        "no",
        "--snapshot-ignore",
        "--snapshot-create=false",
        "--ignore-complexity=false",
        "--report-ignored=false",
        ".",
    )
    assert json.loads(result.stdout) == {
        "results": [
            {"check_id": "cognitive-complexity", "status": "passed", "messages": []}
        ]
    }


@pytest.mark.parametrize("tool_exit_code", [0, 1])
def test_cli_reports_excessive_cognitive_complexity_as_unhealthy(
    app: typer.Typer,
    cli_runner: CliRunner,
    complexipy_config_file: Path,
    processes: FakeProcesses,
    tool_exit_code: int,
) -> None:
    processes.exit_code = tool_exit_code
    processes.stdout = (
        f"{complexipy_config_file.parent}/src/my app.py parse 11\n"
        "./src/service.py Service::execute 16\n"
    )

    result = cli_runner.invoke(
        app,
        ["check", "--config", str(complexipy_config_file), "--format", "json"],
    )

    assert result.exit_code == ExitCode.UNHEALTHY
    assert result.stderr == ""
    assert json.loads(result.stdout) == {
        "results": [
            {
                "check_id": "cognitive-complexity",
                "status": "failed",
                "messages": [
                    "src/my app.py: parse has cognitive complexity 11.",
                    "src/service.py: Service::execute has cognitive complexity 16.",
                ],
            }
        ]
    }


@pytest.mark.parametrize(
    ("tool_exit_code", "stdout", "stderr", "diagnostic"),
    [
        (0, "not a plain report", "", "not a plain report"),
        (
            1,
            (
                "src/app.py parse 11\n"
                "error: Failed to process src/broken.py - Please check file/folder "
                "exists or check syntax\n"
            ),
            "",
            "Failed to process src/broken.py",
        ),
        (1, "", "Failed to download complexipy", "Failed to download complexipy"),
        (2, "argument details", "error: invalid arguments", "invalid arguments"),
        (
            0,
            "",
            "Failed to parse /project/complexipy.toml: expected a table\n",
            "Failed to parse /project/complexipy.toml",
        ),
        (
            1,
            "src/app.py parse 11\n",
            "Invalid config in /project/pyproject.toml: invalid type\n",
            "Invalid config in /project/pyproject.toml",
        ),
        (1, "", "", "without reporting findings"),
    ],
)
def test_cli_reports_complexipy_execution_and_output_errors(
    app: typer.Typer,
    cli_runner: CliRunner,
    complexipy_config_file: Path,
    processes: FakeProcesses,
    tool_exit_code: int,
    stdout: str,
    stderr: str,
    diagnostic: str,
) -> None:
    processes.exit_code = tool_exit_code
    processes.stdout = stdout
    processes.stderr = stderr

    result = cli_runner.invoke(
        app,
        ["check", "--config", str(complexipy_config_file), "--format", "json"],
    )

    assert result.exit_code == ExitCode.ERROR
    assert result.stderr == ""
    results = json.loads(result.stdout)["results"]
    assert len(results) == 1
    assert results[0]["check_id"] == "cognitive-complexity"
    assert results[0]["status"] == "error"
    assert diagnostic in "\n".join(results[0]["messages"])


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
    app: typer.Typer,
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
    app: typer.Typer,
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


@pytest.mark.parametrize(
    "stdout", ["not json", '[{"description": "Missing location"}]']
)
def test_cli_reports_malformed_ty_output_as_an_error(
    app: typer.Typer,
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
    app: typer.Typer,
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
    app: typer.Typer,
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
    app: typer.Typer,
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
    app: typer.Typer,
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


@pytest.mark.parametrize(
    ("pyproject", "first_status", "expected_exit_code"),
    [
        ("[tool.importlinter]\ncontracts = []\n", "skipped", ExitCode.SUCCESS),
        ("[tool.importlinter\n", "error", ExitCode.ERROR),
    ],
)
def test_cli_still_runs_later_checks_after_an_import_linter_prerequisite_result(
    app: typer.Typer,
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


@pytest.mark.parametrize("operation", ["check"])
@pytest.mark.parametrize("fmt", [OutputFormat.TEXT, OutputFormat.JSON])
def test_cli_reports_every_check_after_an_individual_tool_error(
    cli_runner: CliRunner,
    tmp_path: Path,
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
    command = ScriptedCommand(outcomes=outcomes, runnable=True)
    app = make_app(commands=FixedCommandRegistry({CommandName.RUFF: command}))

    result = cli_runner.invoke(
        app,
        [operation, "--config", str(config_file), "--format", fmt.value],
    )

    assert result.exit_code == ExitCode.ERROR
    assert result.stderr == ""
    assert [check.id for check in command.executed] == list(check_ids)
    assert command.roots == [tmp_path] * len(check_ids)
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
) -> None:
    command = ScriptedCommand(
        outcomes={"ruff": RuntimeError("adapter bug")}, runnable=True
    )
    app = make_app(commands=FixedCommandRegistry({CommandName.RUFF: command}))

    result = cli_runner.invoke(app, ["check", "--config", str(config_file)])

    assert result.exit_code == ExitCode.ERROR
    assert result.stdout == ""
    assert "general error: RuntimeError: adapter bug" in result.stderr


@pytest.mark.parametrize("operation", ["check"])
def test_check_reports_a_bad_config_option_without_typers_wording(
    app: typer.Typer,
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
    app: typer.Typer,
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


@pytest.mark.parametrize("operation", ["check"])
def test_check_rejects_an_unknown_format(
    app: typer.Typer,
    cli_runner: CliRunner,
    operation: str,
) -> None:
    result = cli_runner.invoke(app, [operation, "--format", "yaml"])

    assert result.exit_code == ExitCode.ERROR


@pytest.mark.parametrize(
    "command",
    [["agents", "install"], ["agents", "uninstall"]],
)
def test_unimplemented_commands_fail_loudly(
    app: typer.Typer,
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
        LoggingConfigurator(
            logger=logging.getLogger(LOGGER_NAME),
            handler=RichHandler(console=Console(stderr=True), markup=False),
        ).configure(debug=debug)

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


CLI_SCHEMA_PATH = Path(__file__).parents[5] / "checksmith-cli.yaml"


def test_init_root_option_matches_the_cli_schema_and_appears_in_help(
    app: typer.Typer,
    cli_runner: CliRunner,
) -> None:
    schema = yaml.safe_load(CLI_SCHEMA_PATH.read_text(encoding="utf-8"))
    command = next(
        entry for entry in schema["command"]["commands"] if entry["name"] == "init"
    )
    declared = next(
        option for option in command["options"] if option["name"] == "--project-root"
    )
    root = typer.main.get_command(app)
    assert isinstance(root, TyperGroup)
    option = next(
        parameter
        for parameter in root.commands["init"].params
        if parameter.name == "project_root"
    )

    assert command["interactive"] is True
    assert isinstance(option, TyperOption)
    assert option.opts == [declared["name"]]
    assert option.required is declared["required"] is False
    assert option.nargs == len(declared["arguments"]) == 1
    assert declared["arguments"][0]["required"] is True
    assert all(parameter.name != "fmt" for parameter in root.commands["init"].params)
    assert all(option["name"] != "--format" for option in command["options"])

    result = cli_runner.invoke(app, ["init", "--help"])

    assert result.exit_code == ExitCode.SUCCESS
    assert "--project-root" in result.stdout
    assert "--astcheck-policy" in result.stdout
    assert "--format" not in result.stdout
    assert "current directory" in result.stdout


@pytest.mark.parametrize("operation", ["check"])
def test_check_selector_matches_the_cli_schema_and_appears_in_help(
    app: typer.Typer,
    cli_runner: CliRunner,
    operation: str,
) -> None:
    schema = yaml.safe_load(CLI_SCHEMA_PATH.read_text(encoding="utf-8"))
    command = next(
        entry for entry in schema["command"]["commands"] if entry["name"] == operation
    )
    declared = next(
        option for option in command["options"] if option["name"] == "--check"
    )
    root = typer.main.get_command(app)
    assert isinstance(root, TyperGroup)
    option = next(
        parameter
        for parameter in root.commands[operation].params
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


def test_command_names_match_the_cli_schema(
    app: typer.Typer,
) -> None:
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
    }


def test_exit_code_values_match_the_cli_schema() -> None:
    assert ExitCode.SUCCESS == 0
    assert ExitCode.UNHEALTHY == 1
    assert ExitCode.ERROR == 2


def test_exit_code_is_usable_as_an_int() -> None:
    assert int(ExitCode.UNHEALTHY) == 1


def test_production_composition_preserves_the_executable_entry_point(
    cli_runner: CliRunner,
) -> None:
    result = cli_runner.invoke(production_app, ["--version"])
    assert result.exit_code == ExitCode.SUCCESS
    assert result.stdout.strip() == f"checksmith {__version__}"


def test_separate_apps_keep_their_own_command_dependencies(
    cli_runner: CliRunner,
    config_file: Path,
) -> None:
    passing = FakeProcesses()
    passing.stdout = "[]"
    failing = FakeProcesses()
    failing.exit_code = 2
    failing.stderr = "invalid tool config"
    first = make_app(commands=make_command_factory(executor=passing))
    second = make_app(commands=make_command_factory(executor=failing))
    arguments = ["check", "--config", str(config_file), "--format", "json"]

    assert cli_runner.invoke(first, arguments).exit_code == ExitCode.SUCCESS
    assert cli_runner.invoke(second, arguments).exit_code == ExitCode.ERROR
    assert cli_runner.invoke(first, arguments).exit_code == ExitCode.SUCCESS
    assert len(passing.started) == 2
    assert len(failing.started) == 1
