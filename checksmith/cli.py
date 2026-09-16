"""Typer command-line interface for Checksmith."""

from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, NoReturn

import typer
from rich.console import Console
from typer.core import TyperGroup

from checksmith import __version__
from checksmith.config import Config
from checksmith.dtos import ExitCode
from checksmith.errors import ChecksmithError
from checksmith.execution import Runner
from checksmith.outputs.base import CliOutput
from checksmith.tools.tool import Tool
from checksmith.tools.tool_factory import ToolFactory

HELP_OPTION_NAMES = ["-h", "--help"]
CONTEXT_SETTINGS = {"help_option_names": HELP_OPTION_NAMES}


class OutputFormat(StrEnum):
    """Rendering format for a command's output."""

    TEXT = "text"
    JSON = "json"


FormatOption = Annotated[
    OutputFormat,
    typer.Option("--format", help="Output format for this command."),
]

ConfigOption = Annotated[
    Path,
    typer.Option(
        "--config",
        help="Config file describing the checks to run",
    ),
]

console = Console()


def _emit(output: CliOutput, fmt: OutputFormat) -> NoReturn:
    """Render an operation's output and exit with the code it implies."""
    if fmt is OutputFormat.JSON:
        # Plain echo: Rich would add markup interpretation and line wrapping.
        typer.echo(output.model_dump_json(indent=2))
    else:
        console.print(output)
    raise typer.Exit(output.exit_code)


class ChecksmithGroup(TyperGroup):
    def invoke(self, ctx: Any) -> object:
        try:
            return super().invoke(ctx)
        except typer.TyperException, typer.Exit, typer.Abort:
            raise
        except ChecksmithError as error:
            typer.echo(f"checksmith error: {error}", err=True)
            raise typer.Exit(ExitCode.ERROR) from error
        except Exception as error:
            typer.echo(f"general error: {type(error).__name__}: {error}", err=True)
            raise typer.Exit(ExitCode.ERROR) from error


app = typer.Typer(
    name="checksmith",
    cls=ChecksmithGroup,
    help="Manage coding-agent integrations and enforce deterministic quality gates when an agent finishes.",
    context_settings=CONTEXT_SETTINGS,
    no_args_is_help=True,
    add_completion=False,
)

agents_app = typer.Typer(
    name="agents",
    help="Manage integrations with supported coding agents.",
    context_settings=CONTEXT_SETTINGS,
    no_args_is_help=True,
)

app.add_typer(agents_app)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"checksmith {__version__}")
        raise typer.Exit(ExitCode.SUCCESS)


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            help="Show the installed Checksmith version.",
            callback=_version_callback,
            is_eager=True,
        ),
    ] = False,
) -> None:
    """Manage coding-agent integrations and enforce project checks."""


@agents_app.command("install")
def agents_install() -> None:
    """Register integrations with selected coding agents.

    Operation id: ``agents.install``. Output kind: ``AgentsInstall``.
    """
    raise NotImplementedError


@agents_app.command("uninstall")
def agents_uninstall() -> None:
    """Remove integrations from selected coding agents.

    Operation id: ``agents.uninstall``. Output kind: ``AgentsUninstall``.
    """
    raise NotImplementedError


@app.command("init")
def init() -> None:
    """Create the project's Checksmith YAML configuration.

    Operation id: ``init``. Output kind: ``Init``.
    """
    raise NotImplementedError


@app.command("check")
def check(
    config_path: ConfigOption,
    fmt: FormatOption = OutputFormat.TEXT,
) -> None:
    """Execute the project's configured checks and report their results."""
    config = Config.from_path(
        config_path=config_path,
        working_directory=Path.cwd(),
    )
    tool_factory = ToolFactory(config=config)
    tools: tuple[Tool, ...] = tool_factory.get_tools()
    runner = Runner(tools=tools)
    _emit(runner.check(), fmt)


if __name__ == "__main__":
    app()
