"""Output model for the ``check`` operation."""

from rich.console import RenderableType
from rich.table import Table
from rich.text import Text

from checksmith.dtos import CheckResult, ErrorSeverity, ExitCode
from checksmith.outputs.base import CliOutput


class CheckOutput(CliOutput):
    """Result of executing the project's configured checks."""

    results: tuple[CheckResult, ...] = ()

    @property
    def exit_code(self) -> ExitCode:
        if any(result.severity is ErrorSeverity.ERROR for result in self.results):
            return ExitCode.ERROR
        if any(result.severity is ErrorSeverity.FAILURE for result in self.results):
            return ExitCode.UNHEALTHY
        return ExitCode.SUCCESS

    def __rich__(self) -> RenderableType:
        table = Table(title="Checksmith")
        table.add_column("Check")
        table.add_column("Status")
        table.add_column("Messages")
        for result in self.results:
            match result.severity:
                case ErrorSeverity.ERROR:
                    status = "[red]ERROR[/red]"
                case ErrorSeverity.FAILURE:
                    status = "[red]FAIL[/red]"
                case None:
                    status = "[green]PASS[/green]"
            table.add_row(
                Text(result.check_id), status, Text("\n".join(result.messages))
            )
        return table
