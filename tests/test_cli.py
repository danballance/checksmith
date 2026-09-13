"""Tests for :mod:`checksmith.cli`."""

import json
from collections.abc import Callable

import pytest
import typer
from typer.testing import CliRunner

from checksmith import __version__
from checksmith.cli import OutputFormat, _ChecksmithGroup, app
from checksmith.dtos import ExitCode


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


def test_check_full_renders_a_table_by_default(cli_runner: CliRunner) -> None:
    result = cli_runner.invoke(app, ["check", "full"])

    assert result.exit_code == ExitCode.SUCCESS
    assert "Checksmith" in result.stdout


def test_check_full_emits_json_on_request(cli_runner: CliRunner) -> None:
    result = cli_runner.invoke(app, ["check", "full", "--format", "json"])

    assert result.exit_code == ExitCode.SUCCESS
    assert json.loads(result.stdout) == {"results": []}


def test_check_full_rejects_an_unknown_format(cli_runner: CliRunner) -> None:
    result = cli_runner.invoke(app, ["check", "full", "--format", "yaml"])

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
    lone command into a bare ``Command``, which would bypass ``cls``.
    """
    built = typer.Typer(cls=_ChecksmithGroup)

    @built.callback()
    def _root() -> None: ...

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


def test_exit_code_values_match_the_cli_schema() -> None:
    assert ExitCode.SUCCESS == 0
    assert ExitCode.UNHEALTHY == 1
    assert ExitCode.ERROR == 2


def test_exit_code_is_usable_as_an_int() -> None:
    assert int(ExitCode.UNHEALTHY) == 1
