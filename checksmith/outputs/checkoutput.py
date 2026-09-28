"""Output model for the ``check`` operation."""

from rich.console import Group, RenderableType
from rich.panel import Panel
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
        details: list[Panel] = []
        for result in self.results:
            match result.status:
                case CheckStatus.ERROR:
                    status = Text("ERROR", style="red")
                case CheckStatus.FAILED:
                    status = Text("FAIL", style="red")
                case CheckStatus.PASSED:
                    status = Text("PASS", style="green")
                case CheckStatus.SKIPPED:
                    status = Text("SKIP", style="yellow")
            if any("\n" in message for message in result.messages):
                summary, separator, remainder = result.messages[0].partition("\n")
                detail_messages = list(result.messages[1:])
                if separator:
                    detail_messages.insert(0, remainder)
                content = Text()
                for index, message in enumerate(detail_messages):
                    if index:
                        content.append("\n\n")
                    heading, newline, body = message.partition("\n")
                    content.append(heading, style="bold")
                    content.append(newline + body)
                details.append(
                    Panel(
                        content,
                        title=Text.assemble(Text(result.check_id), " | ", status),
                        title_align="left",
                    )
                )
                messages = summary
            else:
                messages = "\n".join(result.messages)
            table.add_row(Text(result.check_id), status, Text(messages))
        return Group(table, *details) if details else table
