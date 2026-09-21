"""Output model for the ``check`` operation."""

from rich.console import RenderableType
from rich.table import Table
from rich.text import Text

from checksmith.dtos import CheckResult, CheckStatus, ExitCode
from checksmith.outputs.base import CliOutput


class CheckOutput(CliOutput):
    """Result of executing the project's configured checks."""

    results: tuple[CheckResult, ...] = ()

    @property
    def exit_code(self) -> ExitCode:
        if any(result.status is CheckStatus.ERROR for result in self.results):
            return ExitCode.ERROR
        if any(result.status is CheckStatus.FAILED for result in self.results):
            return ExitCode.UNHEALTHY
        return ExitCode.SUCCESS

    def __rich__(self) -> RenderableType:
        table = Table(title="Checksmith")
        table.add_column("Check")
        table.add_column("Status")
        table.add_column("Messages")
        for result in self.results:
            match result.status:
                case CheckStatus.ERROR:
                    status = "[red]ERROR[/red]"
                case CheckStatus.FAILED:
                    status = "[red]FAIL[/red]"
                case CheckStatus.PASSED:
                    status = "[green]PASS[/green]"
                case CheckStatus.SKIPPED:
                    status = "[yellow]SKIP[/yellow]"
            table.add_row(
                Text(result.check_id), status, Text("\n".join(result.messages))
            )
        return table
