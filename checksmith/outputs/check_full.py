"""Output model for the ``check full`` operation."""

from pydantic import BaseModel
from rich.console import RenderableType
from rich.table import Table

from checksmith.dtos import ExitCode
from checksmith.outputs.base import CliOutput


class ToolResult(BaseModel):
    """Outcome of running a single configured tool."""

    name: str
    """Name of the tool that was run."""

    failed: bool = False
    """Whether the tool reported a coding standard violation."""

    findings: tuple[str, ...] = ()
    """Human-readable descriptions of what the tool reported."""


class CheckFull(CliOutput):
    """Result of executing the complete configured check suite."""

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
