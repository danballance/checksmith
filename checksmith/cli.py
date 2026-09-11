"""Typer command-line interface for Checksmith.

The structure here mirrors ``checksmith-cli.yaml`` (the OpenCLI schema). Command
bodies are intentionally left unimplemented; only the interface is defined.
"""

from enum import IntEnum
from typing import Annotated

import typer

from checksmith import __version__

HELP_OPTION_NAMES = ["-h", "--help"]
CONTEXT_SETTINGS = {"help_option_names": HELP_OPTION_NAMES}


class ExitCode(IntEnum):
    """Process exit codes defined by the Checksmith CLI schema."""

    SUCCESS = 0
    """The operation completed successfully."""

    UNHEALTHY = 1
    """The operation completed but found a failing or unhealthy state."""

    INVALID_INPUT = 2
    """The command, configuration, or input was invalid."""

    EXECUTION_ERROR = 3
    """Checksmith or an external tool could not complete the operation."""


app = typer.Typer(
    name="checksmith",
    help="Manage coding-agent integrations and enforce project checks.",
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

check_app = typer.Typer(
    name="check",
    help="Execute project checks and report their results.",
    context_settings=CONTEXT_SETTINGS,
    no_args_is_help=True,
)

app.add_typer(agents_app)
app.add_typer(check_app)


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

    Operation id: ``agents.install``. Output kind: ``OutputAgentsInstall``.
    """
    raise NotImplementedError


@agents_app.command("uninstall")
def agents_uninstall() -> None:
    """Remove integrations from selected coding agents.

    Operation id: ``agents.uninstall``. Output kind: ``OutputAgentsUninstall``.
    """
    raise NotImplementedError


@app.command("init")
def init() -> None:
    """Create the project's Checksmith TOML configuration.

    Operation id: ``init``. Output kind: ``OutputInit``.
    """
    raise NotImplementedError


@check_app.command("full")
def check_full() -> None:
    """Execute the complete configured check suite and report results.

    Operation id: ``check.full``. Output kind: ``OutputCheckFull``.
    """
    raise NotImplementedError


if __name__ == "__main__":
    app()
