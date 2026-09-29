from enum import StrEnum
from typing import NoReturn

import typer
from rich.console import Console

from checksmith.adapters.driving.cli.rendering import render_output
from checksmith.domain.results import CliOutput


class OutputFormat(StrEnum):
    TEXT = "text"
    JSON = "json"


class ConsoleProcessOutput:
    def __init__(self, console: Console) -> None:
        self._console = console
        self._partial_line = False

    def write(self, text: str) -> None:
        if not text:
            return
        self._console.file.write(text)
        self._console.file.flush()
        self._partial_line = not text.endswith("\n")

    def finish(self) -> None:
        if self._partial_line:
            self.write("\n")


class OutputPresenter:
    def __init__(self, console: Console) -> None:
        self._console = console

    def process_output(self) -> ConsoleProcessOutput:
        return ConsoleProcessOutput(console=self._console)

    def emit(self, output: CliOutput, fmt: OutputFormat) -> NoReturn:
        if fmt is OutputFormat.JSON:
            typer.echo(output.model_dump_json(indent=2))
        else:
            self._console.print(render_output(output))
        raise typer.Exit(output.exit_code)
