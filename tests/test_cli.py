"""Tests for :mod:`checksmith.cli`."""

import json
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any

import pytest
import typer
import yaml
from typer.core import TyperGroup
from typer.testing import CliRunner

from checksmith import __version__
from checksmith.cli import (
    ChecksmithGroup,
    DebugOption,
    OutputFormat,
    _emit,
    app,
)
from checksmith.dtos import CheckResult, ExitCode
from checksmith.errors import ConfigSyntaxError
from checksmith.logs import configure_logging
from checksmith.outputs.checkoutput import CheckOutput
from tests.conftest import FakeProcesses


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


def test_check_requires_a_config_to_be_named(cli_runner: CliRunner) -> None:
    """Click owns this one. The error boundary re-raises rather than relabels it."""
    result = cli_runner.invoke(app, ["check"])

    assert result.exit_code == ExitCode.ERROR
    assert "Missing option" in result.stderr
    assert "--config" in result.stderr


def test_check_loads_the_named_config_and_runs_what_it_declares(
    cli_runner: CliRunner,
    config_tree: Path,
    processes: FakeProcesses,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Configuration and execution succeed; reading the output is what is missing.

    The working directory the tool was started in is the resolved project root,
    which is the whole of that resolution proved from the command line in.
    """
    monkeypatch.chdir(config_tree)

    result = cli_runner.invoke(
        app,
        ["check", "--config", ".checksmith/checksmith.yaml"],
    )

    assert processes.started[0].cwd == config_tree
    assert result.exit_code == ExitCode.ERROR
    assert "NotImplementedError" in result.stderr
    assert "Reading ruff output is not implemented yet" in result.stderr
    assert "check: ruff" in result.stderr


def test_check_accepts_an_absolute_config_path(
    cli_runner: CliRunner,
    config_path: Path,
    config_tree: Path,
    tmp_path: Path,
    processes: FakeProcesses,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A relative path resolves from here; an absolute one ignores here."""
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    result = cli_runner.invoke(app, ["check", "--config", str(config_path)])

    assert processes.started[0].cwd == config_tree
    assert result.exit_code == ExitCode.ERROR
    assert "check: ruff" in result.stderr


def test_check_reports_a_bad_config_option_without_typers_wording(
    cli_runner: CliRunner,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Typer does no path validation, so the config file is opened before it complains."""
    monkeypatch.chdir(tmp_path)

    result = cli_runner.invoke(app, ["check", "--config", "absent.yaml"])

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


def test_emit_renders_json_when_asked(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(typer.Exit):
        _emit(CheckOutput(), OutputFormat.JSON)

    assert json.loads(capsys.readouterr().out) == {"results": []}


def test_emit_renders_a_table_by_default(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(typer.Exit):
        _emit(CheckOutput(results=(CheckResult(check_id="ruff"),)), OutputFormat.TEXT)

    captured = capsys.readouterr().out
    assert "Checksmith" in captured
    assert "ruff" in captured


def test_emit_exits_with_the_code_its_output_implies(
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = CheckOutput(results=(CheckResult(check_id="ruff", failed=True),))

    with pytest.raises(typer.Exit) as raised:
        _emit(output, OutputFormat.TEXT)

    assert raised.value.exit_code == ExitCode.UNHEALTHY


def test_check_rejects_an_unknown_format(cli_runner: CliRunner) -> None:
    result = cli_runner.invoke(app, ["check", "--format", "yaml"])

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
            config_path=Path("/project/.checksmith/checksmith.yaml"),
            problem=(
                'expected a mapping at the top level in '
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
    }


def test_exit_code_values_match_the_cli_schema() -> None:
    assert ExitCode.SUCCESS == 0
    assert ExitCode.UNHEALTHY == 1
    assert ExitCode.ERROR == 2


def test_exit_code_is_usable_as_an_int() -> None:
    assert int(ExitCode.UNHEALTHY) == 1
