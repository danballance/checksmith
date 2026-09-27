"""Typer command-line interface for Checksmith."""

import logging
import os
from importlib.resources import files
from pathlib import Path
from typing import Annotated, Any, Final

import typer
from rich.console import Console
from rich.logging import RichHandler
from typer.core import TyperGroup

from checksmith import __version__
from checksmith.checking import CheckService
from checksmith.commands.import_linter import ImportLinterPrerequisites
from checksmith.commands.pytest import PackagedLauncherSource
from checksmith.commands.registry import CommandFactory
from checksmith.configuration_loading import ConfigLoader, LocalYamlConfigSource
from checksmith.dtos import ExitCode
from checksmith.errors import ChecksmithError
from checksmith.initialization import (
    Initializer,
    LocalInitializationFilesystem,
    PackagedAssetSource,
    YamlConfigRenderer,
)
from checksmith.logs import LOGGER_NAME, LoggingConfigurator
from checksmith.packages import UvPackage
from checksmith.prerequisites import LocalProjectFiles, UvProjectPrerequisites
from checksmith.presentation import OutputFormat, OutputPresenter
from checksmith.processes import LocalProcessRuntime, SubprocessExecutor

HELP_OPTION_NAMES = ["-h", "--help"]
CONTEXT_SETTINGS = {"help_option_names": HELP_OPTION_NAMES}

DEBUG_FLAGS: Final = ("--debug", "-d")
"""Spellings of the debug switch, which may appear anywhere on the line."""

logger = logging.getLogger(__name__)


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

CheckOption = Annotated[
    str | None,
    typer.Option(
        "--check",
        metavar="ID",
        help="Select one configured check by its exact ID; omit to select all.",
    ),
]

ProjectRootOption = Annotated[
    Path | None,
    typer.Option(
        "--project-root",
        metavar="PATH",
        help=(
            "Existing project directory; relative paths resolve from the current "
            "directory. Omit to confirm the project root interactively."
        ),
    ),
]

DebugOption = Annotated[
    bool,
    typer.Option(*DEBUG_FLAGS, help="Write debug logging to stderr."),
]


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


class CliApplication:
    def __init__(
        self,
        checks: CheckService,
        initializer: Initializer,
        presenter: OutputPresenter,
        logging: LoggingConfigurator,
    ) -> None:
        self._checks = checks
        self._initializer = initializer
        self._presenter = presenter
        self._logging = logging

    def build(self) -> typer.Typer:
        application = typer.Typer(
            name="checksmith",
            cls=ChecksmithGroup,
            help="Manage coding-agent integrations and enforce deterministic quality gates when an agent finishes.",
            context_settings=CONTEXT_SETTINGS,
            no_args_is_help=True,
            add_completion=False,
        )
        agents = typer.Typer(
            name="agents",
            help="Manage integrations with supported coding agents.",
            context_settings=CONTEXT_SETTINGS,
            no_args_is_help=True,
        )
        agents.command("install")(self.agents_install)
        agents.command("uninstall")(self.agents_uninstall)
        application.add_typer(agents)
        application.callback()(self.main)
        application.command("init")(self.init)
        application.command("check")(self.check)
        return application

    @staticmethod
    def _version_callback(value: bool) -> None:
        if value:
            typer.echo(f"checksmith {__version__}")
            raise typer.Exit(ExitCode.SUCCESS)

    def main(
        self,
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
        """Manage coding-agent integrations and enforce project checks."""
        self._logging.configure(debug=debug)

    def agents_install(self) -> None:
        """Register integrations with selected coding agents.

        Operation id: ``agents.install``. Output kind: ``AgentsInstall``.
        """
        raise NotImplementedError

    def agents_uninstall(self) -> None:
        """Remove integrations from selected coding agents.

        Operation id: ``agents.uninstall``. Output kind: ``AgentsUninstall``.
        """
        raise NotImplementedError

    def init(self, project_root: ProjectRootOption = None) -> None:
        """Create the bundled configuration files in the current directory.

        Operation id: ``init``. Output kind: ``Init``.
        """
        destination = Path.cwd()
        if project_root is None:
            project_root = Path(typer.prompt("Project root", default=str(destination)))
        self._presenter.emit(
            self._initializer.initialize(
                destination=destination, project_root=project_root
            ),
            OutputFormat.TEXT,
        )

    def check(
        self,
        config_file: ConfigOption,
        fmt: FormatOption = OutputFormat.TEXT,
        check_id: CheckOption = None,
    ) -> None:
        """Execute the project's configured checks and report their results."""
        self._presenter.emit(
            self._checks.check(
                config_file=config_file,
                working_directory=Path.cwd(),
                check_id=check_id,
            ),
            fmt,
        )


app = CliApplication(
    checks=CheckService(
        loader=ConfigLoader(source=LocalYamlConfigSource()),
        commands=CommandFactory(
            executor=SubprocessExecutor(runtime=LocalProcessRuntime()),
            uv_prerequisites=UvProjectPrerequisites(
                environment=os.environ, files=LocalProjectFiles()
            ),
            import_linter_prerequisites=ImportLinterPrerequisites(
                files=LocalProjectFiles()
            ),
            launcher_source=PackagedLauncherSource(
                resource=files("checksmith.commands") / "_pytest_launcher.py"
            ),
            pytest_package=UvPackage(),
        ),
    ),
    initializer=Initializer(
        assets=PackagedAssetSource(
            directory=files("checksmith") / "assets" / "default"
        ),
        renderer=YamlConfigRenderer(),
        filesystem=LocalInitializationFilesystem(),
    ),
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


if __name__ == "__main__":
    app()
