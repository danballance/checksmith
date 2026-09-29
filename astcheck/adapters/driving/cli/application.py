import sys
from contextlib import redirect_stdout
from pathlib import Path
from typing import Annotated

import typer

from astcheck.adapters.driving.cli.presentation import OutputFormat, ReportPresenter
from astcheck.adapters.driving.cli.setup import (
    ConfigurationFormat,
    ConfigurationResponse,
)
from astcheck.application.use_cases.analysis import AnalysisService
from astcheck.application.use_cases.configuration import ConfigureService
from astcheck.domain.errors import AstcheckError


class TyperOutput:
    def write(self, *, text: str) -> None:
        typer.echo(text)


class AstcheckApplication:
    def __init__(
        self,
        *,
        analysis: AnalysisService,
        presenter: ReportPresenter,
        configuration: ConfigureService,
    ) -> None:
        self._analysis = analysis
        self._presenter = presenter
        self._configuration = configuration

    def build(self) -> typer.Typer:
        application = typer.Typer(
            name="astcheck", add_completion=False, no_args_is_help=True
        )
        application.callback()(self.main)
        application.command("check")(self.check)
        application.command("configure")(self.configure)
        return application

    def main(self) -> None:
        pass

    def check(
        self,
        config: Annotated[Path, typer.Option("--config")],
        format: Annotated[OutputFormat, typer.Option("--format")],
    ) -> None:
        report = self._analysis.run(config_file=config)
        self._presenter.emit(report=report, format=format)
        raise typer.Exit(report.exit_code)

    def configure(
        self,
        project_root: Annotated[
            Path, typer.Option("--project-root", exists=True, file_okay=False)
        ],
        config_file: Annotated[Path, typer.Option("--config-file")],
        format: Annotated[ConfigurationFormat, typer.Option("--format")],
        policy: Annotated[Path | None, typer.Option("--policy")] = None,
        interactive: Annotated[bool, typer.Option("--interactive")] = False,
    ) -> None:
        if (policy is not None) == interactive:
            typer.echo("Specify exactly one of --policy or --interactive", err=True)
            raise typer.Exit(2)
        try:
            with redirect_stdout(sys.stderr):
                configuration_yaml = self._configuration.run(
                    project_root=project_root,
                    config_file=config_file,
                    policy_file=policy,
                )
        except (typer.Abort, KeyboardInterrupt) as error:
            typer.echo("ASTcheck configuration cancelled", err=True)
            raise typer.Exit(2) from error
        except AstcheckError as error:
            typer.echo(str(error), err=True)
            raise typer.Exit(2) from error
        typer.echo(
            ConfigurationResponse(
                schema_version=1, configuration_yaml=configuration_yaml
            ).model_dump_json()
        )
