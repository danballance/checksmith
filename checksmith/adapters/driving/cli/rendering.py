from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from checksmith.domain.models import CheckStatus
from checksmith.domain.results import CheckOutput, CliOutput, InitOutput


def render_checks(output: CheckOutput) -> RenderableType:
    table = Table(title="Checksmith")
    table.add_column("Check")
    table.add_column("Status")
    table.add_column("Messages")
    details: list[Panel] = []
    for result in output.results:
        match result.status:
            case CheckStatus.ERROR:
                status = Text("ERROR", style="red")
            case CheckStatus.FAILED:
                status = Text("FAIL", style="red")
            case CheckStatus.PASSED:
                status = Text("PASS", style="green")
            case CheckStatus.SKIPPED:
                status = Text("SKIP", style="yellow")
        has_details = any("\n" in message for message in result.messages)
        if result.status is CheckStatus.PASSED and has_details:
            messages = result.messages[0].partition("\n")[0]
        elif result.status in (CheckStatus.FAILED, CheckStatus.ERROR) and has_details:
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


def render_output(output: CliOutput) -> RenderableType:
    if isinstance(output, CheckOutput):
        return render_checks(output)
    if isinstance(output, InitOutput):
        return Text(
            f"Project root: {output.project_root}\nCreated:\n"
            + "\n".join(f"  {path}" for path in output.created_files)
        )
    raise TypeError(f"No renderer for {type(output).__name__}")
