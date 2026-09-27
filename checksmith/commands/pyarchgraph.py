from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from checksmith.commands.command import Command
from checksmith.dtos import CheckResult, CheckStatus, CommandName
from checksmith.errors import CheckOutputError


class ReportModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


Text = Annotated[str, Field(min_length=1)]
Count = Annotated[int, Field(ge=0)]
PositiveCount = Annotated[int, Field(gt=0)]
Certainty = Literal["definite", "possible"]
Gate = Literal["structural", "non-typing", "module-body"]


class ImportContext(ReportModel):
    scope: Literal["module", "class", "function"]
    in_function: bool
    typing_only: bool
    conditional: bool
    exception_handler: bool
    package_initializer: bool


class ImportEvidence(ReportModel):
    path: Text
    line: PositiveCount
    column: PositiveCount
    source_segment: str | None
    resolution_kind: Literal["exact_module", "exact_base", "probable_submodule"] | None
    context: ImportContext


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
    dependency_count: PositiveCount
    dependencies: Annotated[tuple[Dependency, ...], Field(min_length=1)] | None


class ImportFinding(ReportModel):
    kind: Literal["unresolved_import"]
    source: Text
    requested: Text | None
    code: Text
    message: Text
    evidence: Annotated[tuple[ImportEvidence, ...], Field(min_length=1)]


Finding = Annotated[CycleFinding | ImportFinding, Field(discriminator="kind")]


class GraphView(ReportModel):
    dependency_count: Count
    cyclic_source_count: Count
    cyclic_dependency_count: Count
    findings: tuple[Finding, ...]


class GraphViews(ReportModel):
    structural: GraphView
    non_typing: GraphView
    module_body: GraphView

    def selected(self, gate: Gate) -> GraphView:
        match gate:
            case "structural":
                return self.structural
            case "non-typing":
                return self.non_typing
            case "module-body":
                return self.module_body


class SourceModule(ReportModel):
    id: Text
    path: Text
    is_package: bool
    parent_package: Text | None
    import_name: Text | None
    binding_status: Literal["bound", "path_only", "shadowed", "ambiguous"]
    analysis_status: Literal["pending", "analyzed", "error"]


class Diagnostic(ReportModel):
    severity: Literal["error", "warning", "info"]
    code: Text
    message: Text
    path: Text | None
    line: PositiveCount | None
    column: Count | None


class ExcludedPath(ReportModel):
    path: Text
    rule: Text
    kind: Literal["file", "directory"]


class TargetBoundary(ReportModel):
    name: Text
    kind: Literal["stub", "native", "generated", "excluded"]
    path: Text | None
    reason: Text
    acknowledged: bool
    evidence: tuple[ImportEvidence, ...]


class Coverage(ReportModel):
    roots: tuple[Text, ...]
    excludes: tuple[Text, ...]
    excluded_paths: tuple[ExcludedPath, ...]
    analyzed_source_count: Count
    diagnostics: tuple[Diagnostic, ...]
    boundaries: tuple[TargetBoundary, ...]
    limitations: tuple[Text, ...]


class PyArchGraphReport(ReportModel):
    schema_version: Literal["0.6"]
    status: Literal["complete", "incomplete"]
    gate: Gate
    sources: tuple[SourceModule, ...]
    coverage: Coverage
    views: GraphViews

    @model_validator(mode="after")
    def validate_coverage_and_source_references(self) -> Self:
        sources = {source.id for source in self.sources}
        if len(sources) != len(self.sources):
            raise ValueError("source IDs must be unique")
        if self.status == "complete":
            if not self.sources or any(
                source.analysis_status != "analyzed" for source in self.sources
            ):
                raise ValueError("complete reports must contain only analyzed sources")
            if self.coverage.analyzed_source_count != len(self.sources):
                raise ValueError("complete report analyzed source count must match sources")
            if any(item.severity == "error" for item in self.coverage.diagnostics):
                raise ValueError("complete reports cannot contain error diagnostics")
            if any(not item.acknowledged for item in self.coverage.boundaries):
                raise ValueError("complete reports cannot contain unacknowledged boundaries")
        for view in (
            self.views.structural,
            self.views.non_typing,
            self.views.module_body,
        ):
            referenced: set[str] = set()
            for finding in view.findings:
                if isinstance(finding, ImportFinding):
                    referenced.add(finding.source)
                    continue
                referenced.update(finding.members)
                referenced.update(finding.definite_members)
                for edge in finding.witness + (
                    finding.dependencies if finding.dependencies is not None else ()
                ):
                    referenced.update((edge.source, edge.target))
            if unknown := referenced - sources:
                raise ValueError(f"findings reference unknown source IDs: {sorted(unknown)}")
        return self


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
        if exit_code not in (0, 1, 2) or (exit_code == 2 and not stdout.strip()):
            raise CheckOutputError(
                check_id=check_id,
                command=self.name,
                summary=f"pyarchgraph exited {exit_code} without an analysis report",
                problem="\n".join(part.strip() for part in (stderr, stdout) if part),
            )
        try:
            report = PyArchGraphReport.model_validate_json(stdout)
        except ValidationError as error:
            raise CheckOutputError(
                check_id=check_id,
                command=self.name,
                summary="Invalid PyArchGraph report on stdout",
                problem=f"Expected a schema 0.6 JSON report: {error}",
            ) from error
        selected = report.views.selected(report.gate)
        incomplete = report.status == "incomplete"
        expected_exit = 2 if incomplete else int(bool(selected.findings))
        if exit_code != expected_exit:
            raise CheckOutputError(
                check_id=check_id,
                command=self.name,
                summary="PyArchGraph exit code disagrees with its report",
                problem=(
                    f"Exit {exit_code} with status {report.status!r}, "
                    f"gate {report.gate!r} and {len(selected.findings)} findings; "
                    f"expected exit {expected_exit}."
                ),
            )
        status = (
            CheckStatus.ERROR
            if incomplete
            else CheckStatus.FAILED if selected.findings else CheckStatus.PASSED
        )
        summary = (
            f"Analysis is {report.status}; gate: {report.gate}; "
            f"sources: {len(report.sources)}; "
            f"analyzed: {report.coverage.analyzed_source_count}."
        )
        messages = [summary, self._view_message(report.gate, selected, True)]
        for gate in ("structural", "non-typing", "module-body"):
            if gate != report.gate:
                messages.append(
                    self._view_message(gate, report.views.selected(gate), False)
                )
        messages.extend(
            self._diagnostic_message(diagnostic)
            for diagnostic in report.coverage.diagnostics
        )
        messages.extend(
            self._boundary_message(boundary) for boundary in report.coverage.boundaries
        )
        labels = {
            source.id: source.import_name
            if source.import_name is not None
            else source.path
            for source in report.sources
        }
        prefix = "Partial observation: " if incomplete else ""
        messages.extend(
            prefix + self._finding_message(finding, labels)
            for finding in selected.findings
        )
        return CheckResult(check_id=check_id, status=status, messages=tuple(messages))

    def _view_message(self, gate: Gate, view: GraphView, selected: bool) -> str:
        label = "Selected view" if selected else "Informational view"
        return (
            f"{label} ({gate}): dependencies: {view.dependency_count}; "
            f"cyclic sources: {view.cyclic_source_count}; "
            f"findings: {len(view.findings)}."
        )

    def _diagnostic_message(self, diagnostic: Diagnostic) -> str:
        location = diagnostic.path or ""
        if diagnostic.line is not None:
            location += f":{diagnostic.line}"
        if diagnostic.column is not None:
            location += f":{diagnostic.column}"
        prefix = f"{location}: " if location else ""
        return (
            f"{prefix}{diagnostic.severity.capitalize()} [{diagnostic.code}]: "
            f"{diagnostic.message}"
        )

    def _boundary_message(self, boundary: TargetBoundary) -> str:
        accepted = "Acknowledged" if boundary.acknowledged else "Unacknowledged"
        location = f" ({boundary.path})" if boundary.path is not None else ""
        evidence = (
            f" {self._evidence_text(boundary.evidence)}" if boundary.evidence else ""
        )
        return (
            f"{accepted} {boundary.kind} boundary {boundary.name}{location}: "
            f"{boundary.reason}.{evidence}"
        )

    def _evidence_text(self, evidence: tuple[ImportEvidence, ...]) -> str:
        locations = []
        for item in evidence:
            context = item.context
            labels = [
                label
                for enabled, label in (
                    (context.typing_only, "typing-only"),
                    (context.in_function, "function-local"),
                    (context.scope == "class", "class body"),
                    (context.conditional, "conditional"),
                    (context.exception_handler, "exception handler"),
                    (context.package_initializer, "package initializer"),
                )
                if enabled
            ]
            locations.append(
                f"{item.path}:{item.line}:{item.column}"
                + (f" ({item.source_segment})" if item.source_segment else "")
                + (f" [{', '.join(labels)}]" if labels else "")
            )
        return "; ".join(locations)

    def _finding_message(self, finding: Finding, labels: dict[str, str]) -> str:
        if isinstance(finding, ImportFinding):
            return (
                f"Unresolved import in {labels[finding.source]}: {finding.message} "
                f"[{finding.code}]. {self._evidence_text(finding.evidence)}"
            )
        edges = (
            finding.witness if finding.dependencies is None else finding.dependencies
        )
        label = "Witness" if finding.dependencies is None else "Component dependencies"
        dependencies = "; ".join(
            f"{labels[edge.source]} -> {labels[edge.target]} "
            f"at {self._evidence_text(edge.evidence)}"
            for edge in edges
        )
        if finding.definite_members:
            definite = ", ".join(labels[member] for member in finding.definite_members)
            summary = f"Definite cyclic sources: {definite}."
            possible_members = tuple(
                member
                for member in finding.members
                if member not in finding.definite_members
            )
            if possible_members:
                summary += (
                    " Other component members with possible cycle involvement: "
                    f"{', '.join(labels[member] for member in possible_members)}."
                )
        else:
            members = ", ".join(labels[member] for member in finding.members)
            summary = f"Possible dependency cycle among {members}."
        return f"{summary} {label}: {dependencies}"
