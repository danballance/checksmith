from pathlib import Path

from pydantic import ValidationError

from astcheck.domain.models import AnalysisError, AnalysisReport, SourceLocation
from checksmith.commands.command import CapturedOutputCommand
from checksmith.dtos import CheckResult, CheckStatus, CommandName
from checksmith.errors import CheckOutputError


class AstcheckCommand(CapturedOutputCommand):
    @property
    def name(self) -> CommandName:
        return CommandName.ASTCHECK

    def process_response(
        self,
        *,
        check_id: str,
        project_root: Path,
        exit_code: int,
        stdout: str,
        stderr: str,
    ) -> CheckResult:
        if exit_code not in (0, 1, 2) or not stdout.strip():
            raise CheckOutputError(
                check_id=check_id,
                command=self.name,
                summary=f"astcheck exited {exit_code} without a valid analysis report",
                problem="\n".join(part.strip() for part in (stderr, stdout) if part),
            )
        try:
            report = AnalysisReport.model_validate_json(stdout)
        except ValidationError as error:
            raise CheckOutputError(
                check_id=check_id,
                command=self.name,
                summary="Invalid Astcheck report on stdout",
                problem=f"Expected a schema 1 JSON report: {error}",
            ) from error
        if exit_code != report.exit_code:
            raise CheckOutputError(
                check_id=check_id,
                command=self.name,
                summary="Astcheck exit code disagrees with its report",
                problem=(
                    f"Exit {exit_code} with status {report.status!r}; "
                    f"expected exit {report.exit_code}."
                ),
            )
        status = (
            CheckStatus.ERROR
            if report.status == "error"
            else CheckStatus.FAILED if exit_code == 1 else CheckStatus.PASSED
        )
        finding_count = sum(len(plugin.findings) for plugin in report.plugins)
        messages = [
            (
                f"Analysis is {report.status}; modules: {len(report.modules)}; "
                f"plugins: {len(report.plugins)}; findings: {finding_count}."
            )
        ]
        for plugin in report.plugins:
            for finding in plugin.findings:
                message = (
                    f"{self._location(finding.location)}: "
                    f"[{plugin.plugin_id}/{finding.rule_id}] {finding.message}"
                )
                if finding.related_locations:
                    related = ", ".join(
                        self._location(location)
                        for location in finding.related_locations
                    )
                    message += f" Related locations: {related}."
                messages.append(message)
        messages.extend(self._error_message(error) for error in report.errors)
        return CheckResult(check_id=check_id, status=status, messages=tuple(messages))

    def _location(self, location: SourceLocation) -> str:
        return f"{location.path}:{location.line}:{location.column}"

    def _error_message(self, error: AnalysisError) -> str:
        location = error.path
        if location is None:
            return f"Error: {error.message}"
        if error.line is not None:
            location += f":{error.line}"
        if error.column is not None:
            location += f":{error.column}"
        return f"{location}: Error: {error.message}"
