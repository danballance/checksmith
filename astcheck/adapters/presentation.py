from enum import StrEnum
from typing import Protocol

from astcheck.domain.models import AnalysisReport


class OutputFormat(StrEnum):
    TEXT = "text"
    JSON = "json"


class OutputSink(Protocol):
    def write(self, *, text: str) -> None: ...


class ReportPresenter:
    def __init__(self, *, output: OutputSink) -> None:
        self._output = output

    def emit(self, *, report: AnalysisReport, format: OutputFormat) -> None:
        if format is OutputFormat.JSON:
            self._output.write(text=report.model_dump_json())
            return
        for error in report.errors:
            parts = [error.path] if error.path else []
            if error.line is not None:
                parts.append(str(error.line))
            if error.column is not None:
                parts.append(str(error.column))
            prefix = f"{':'.join(parts)}: " if parts else ""
            self._output.write(text=f"{prefix}astcheck error: {error.message}")
        for plugin in report.plugins:
            for finding in plugin.findings:
                location = finding.location
                self._output.write(
                    text=(
                        f"{location.path}:{location.line}:{location.column}: "
                        f"{plugin.plugin_id}/{finding.rule_id}: {finding.message}"
                    )
                )
                for related in finding.related_locations:
                    self._output.write(
                        text=f"  related: {related.path}:{related.line}:{related.column}"
                    )
        if report.status == "complete":
            count = sum(len(plugin.findings) for plugin in report.plugins)
            self._output.write(
                text=f"Analyzed {len(report.modules)} modules; {count} findings."
            )
