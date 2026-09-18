"""Output model for the ``check`` operation."""

from rich.console import RenderableType
from rich.table import Table

from checksmith.dtos import ExitCode, ToolResult
from checksmith.outputs.base import CliOutput


class CheckOutput(CliOutput):
    """Result of executing the project's configured checks."""

    results: tuple[ToolResult, ...] = ()

    @property
    def exit_code(self) -> ExitCode:
        if any(result.failed for result in self.results):
            return ExitCode.UNHEALTHY
        return ExitCode.SUCCESS

    def __rich__(self) -> RenderableType:
        table = Table(title="Checksmith")
        table.add_column("Tool")
        table.add_column("Status")
        table.add_column("Findings")
        for result in self.results:
            status = "[red]FAIL[/red]" if result.failed else "[green]PASS[/green]"
            table.add_row(result.name, status, "\n".join(result.findings))
        return table
