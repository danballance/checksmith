from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from checksmith.commands.command import Command
from checksmith.dtos import CheckResult, CheckStatus, CommandName
from checksmith.errors import CheckOutputError


class ReportModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


Text = Annotated[str, Field(min_length=1)]
Count = Annotated[int, Field(ge=0)]
PositiveCount = Annotated[int, Field(gt=0)]
Certainty = Literal["definite", "possible"]


class ImportEvidence(ReportModel):
    path: Text
    line: PositiveCount
    column: PositiveCount
    source_segment: str | None
    resolution_kind: (
        Literal["exact_module", "exact_base", "probable_submodule"] | None
    )


class Dependency(ReportModel):
    source: Text
    target: Text
    evidence: Annotated[tuple[ImportEvidence, ...], Field(min_length=1)]


class CycleFinding(ReportModel):
    kind: Literal["cycle"]
    certainty: Certainty
    members: Annotated[tuple[Text, ...], Field(min_length=1)]
    definite_members: tuple[Text, ...]
    witness: Annotated[tuple[Dependency, ...], Field(min_length=1)]


class ForbiddenDependencyFinding(ReportModel):
    kind: Literal["forbidden_dependency"]
    certainty: Certainty
    rules: Annotated[tuple[tuple[Text, Text], ...], Field(min_length=1)]
    witness: Annotated[tuple[Dependency, ...], Field(min_length=1, max_length=1)]


class ImportFinding(ReportModel):
    kind: Literal["unresolved_import"]
    source: Text
    requested: Text | None
    code: Text
    message: Text
    evidence: Annotated[tuple[ImportEvidence, ...], Field(min_length=1)]


Finding = Annotated[
    CycleFinding | ForbiddenDependencyFinding | ImportFinding,
    Field(discriminator="kind"),
]


class PyArchGraphReport(ReportModel):
    schema_version: Literal["0.5"]
    module_count: PositiveCount
    dependency_count: Count
    findings: tuple[Finding, ...]


def _evidence_text(evidence: tuple[ImportEvidence, ...]) -> str:
    return "; ".join(
        f"{item.path}:{item.line}:{item.column}"
        + (f" ({item.source_segment})" if item.source_segment else "")
        for item in evidence
    )


def _finding_message(finding: Finding) -> str:
    if isinstance(finding, ImportFinding):
        return (
            f"Unresolved import in {finding.source}: {finding.message} "
            f"[{finding.code}]. {_evidence_text(finding.evidence)}"
        )
    dependencies = "; ".join(
        f"{edge.source} -> {edge.target} at {_evidence_text(edge.evidence)}"
        for edge in finding.witness
    )
    if isinstance(finding, CycleFinding):
        if finding.definite_members:
            summary = f"Definite cyclic modules: {', '.join(finding.definite_members)}."
            possible_members = tuple(
                member
                for member in finding.members
                if member not in finding.definite_members
            )
            if possible_members:
                summary += (
                    " Other component members with possible cycle involvement: "
                    f"{', '.join(possible_members)}."
                )
        else:
            summary = f"Possible dependency cycle among {', '.join(finding.members)}."
        return f"{summary} Witness: {dependencies}"
    rules = ", ".join(f"{source}:{target}" for source, target in finding.rules)
    return (
        f"{finding.certainty.capitalize()} forbidden dependency "
        f"(rules: {rules}). {dependencies}"
    )


class PyArchGraphCommand(Command):
    @property
    def name(self) -> CommandName:
        return CommandName.PYARCHGRAPH

    def process_response(
        self,
        *,
        check_id: str,
        project_root: Path,
        exit_code: int,
        stdout: str,
        stderr: str,
    ) -> CheckResult:
        if exit_code not in (0, 1):
            raise CheckOutputError(
                check_id=check_id,
                command=self.name,
                summary=f"pyarchgraph exited {exit_code} instead of completing analysis",
                problem="\n".join(part.strip() for part in (stderr, stdout) if part),
            )
        try:
            report = PyArchGraphReport.model_validate_json(stdout)
        except ValidationError as error:
            raise CheckOutputError(
                check_id=check_id,
                command=self.name,
                summary="Invalid PyArchGraph report on stdout",
                problem=f"Expected a schema 0.5 JSON report: {error}",
            ) from error
        failed = bool(report.findings)
        if exit_code != int(failed):
            raise CheckOutputError(
                check_id=check_id,
                command=self.name,
                summary="PyArchGraph exit code disagrees with its findings",
                problem=f"Exit {exit_code} with {len(report.findings)} findings.",
            )
        return CheckResult(
            check_id=check_id,
            status=CheckStatus.FAILED if failed else CheckStatus.PASSED,
            messages=(
                f"Modules: {report.module_count}; dependencies: {report.dependency_count}.",
                *(_finding_message(finding) for finding in report.findings),
            ),
        )
