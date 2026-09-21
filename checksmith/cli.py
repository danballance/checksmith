"""Typer command-line interface for Checksmith."""

import logging
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Final, NoReturn

import typer
from rich.console import Console
from typer.core import TyperGroup

from checksmith import __version__
from checksmith.commands.registry import CommandFactory
from checksmith.config import Config
from checksmith.dtos import ExitCode
from checksmith.errors import ChecksmithError
from checksmith.logs import configure_logging
from checksmith.outputs.base import CliOutput
from checksmith.runner import Runner

HELP_OPTION_NAMES = ["-h", "--help"]
CONTEXT_SETTINGS = {"help_option_names": HELP_OPTION_NAMES}

DEBUG_FLAGS: Final = ("--debug", "-d")
"""Spellings of the debug switch, which may appear anywhere on the line."""

logger = logging.getLogger(__name__)


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

DebugOption = Annotated[
    bool,
    typer.Option(*DEBUG_FLAGS, help="Write debug logging to stderr."),
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
    """The root group: takes the debug switch anywhere, and owns the error boundary."""

    def parse_args(self, ctx: Any, args: list[str]) -> list[str]:
        """Move a debug flag to the front, where the root parser will see it."""
        end = args.index("--") if "--" in args else len(args)
        head, tail = args[:end], args[end:]
        flag = next((token for token in head if token in DEBUG_FLAGS), None)
        if flag is None:
            return super().parse_args(ctx, args)
        rest = [token for token in head if token not in DEBUG_FLAGS]
        return super().parse_args(ctx, [flag, *rest, *tail])

    def invoke(self, ctx: Any) -> object:
        try:
            return super().invoke(ctx)
        except typer.TyperException, typer.Exit, typer.Abort:
            raise
        except ChecksmithError as error:
            # Debug level, never higher: anything emitted without ``--debug``
            # would append to the one line this boundary promises to print.
            logger.debug("checksmith error", exc_info=True)
            typer.echo(f"checksmith error: {error}", err=True)
            raise typer.Exit(ExitCode.ERROR) from error
        except Exception as error:
            logger.debug("unhandled %s", type(error).__name__, exc_info=True)
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
    debug: DebugOption = False,
) -> None:
    """Manage coding-agent integrations and enforce project checks.

    Runs before every command body, so ``--debug`` covers all of them. The
    switch may be given anywhere on the line: ``checksmith --debug check ...``
    and ``checksmith check ... --debug`` mean the same thing.
    """
    configure_logging(debug=debug)


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
    config_file: ConfigOption,
    fmt: FormatOption = OutputFormat.TEXT,
) -> None:
    """Execute the project's configured checks and report their results."""
    config = Config.from_path(
        config_file=config_file,
        working_directory=Path.cwd(),
    )
    runner = Runner(
        checks=config.checks,
        commands=CommandFactory.registry(),
        project_root=config.project_root,
    )
    _emit(runner.check(), fmt)


if __name__ == "__main__":
    app()
