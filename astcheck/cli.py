from pathlib import Path
from typing import Annotated

import typer

from astcheck.adapters.configuration import YamlConfigurationLoader
from astcheck.adapters.parser import PythonAstParser
from astcheck.adapters.plugins import EntryPointPluginRegistry, InstalledPluginCatalog
from astcheck.adapters.presentation import OutputFormat, ReportPresenter
from astcheck.adapters.sources import LocalSourceRepository
from astcheck.application.service import AnalysisService


class TyperOutput:
    def write(self, *, text: str) -> None:
        typer.echo(text)


class AstcheckApplication:
    def __init__(self, *, analysis: AnalysisService, presenter: ReportPresenter) -> None:
        self._analysis = analysis
        self._presenter = presenter

    def build(self) -> typer.Typer:
        application = typer.Typer(
            name="astcheck", add_completion=False, no_args_is_help=True
        )
        application.callback()(self.main)
        application.command("check")(self.check)
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


app = AstcheckApplication(
    analysis=AnalysisService(
        configurations=YamlConfigurationLoader(),
        sources=LocalSourceRepository(),
        parser=PythonAstParser(),
        plugins=EntryPointPluginRegistry(catalog=InstalledPluginCatalog()),
    ),
    presenter=ReportPresenter(output=TyperOutput()),
).build()


if __name__ == "__main__":
    app()
