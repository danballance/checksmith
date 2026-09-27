from enum import StrEnum
from typing import NoReturn

import typer
from rich.console import Console

from checksmith.outputs.base import CliOutput


class OutputFormat(StrEnum):
    TEXT = "text"
    JSON = "json"


class OutputPresenter:
    def __init__(self, console: Console) -> None:
        self._console = console

    def emit(self, output: CliOutput, fmt: OutputFormat) -> NoReturn:
        if fmt is OutputFormat.JSON:
            typer.echo(output.model_dump_json(indent=2))
        else:
            self._console.print(output)
        raise typer.Exit(output.exit_code)
