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
Severity = Literal["error", "warning", "info"]


def _require_known_references(
    references: set[str], known: set[str], label: str
) -> None:
    if unknown := references - known:
        raise ValueError(f"Unknown IDs in {label}: {sorted(unknown)}")


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
    source: Text | None
    target: Text | None
    fact_id: Text | None

    def validate_sources(self, sources: set[str]) -> None:
        _require_known_references(
            {value for value in (self.source, self.target) if value is not None},
            sources,
            "evidence source references",
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
    dependency_count: PositiveCount
    dependencies: Annotated[tuple[Dependency, ...], Field(min_length=1)] | None

    def validate_references(self, nodes: set[str], sources: set[str]) -> None:
        references = set(self.members) | set(self.definite_members)
        for edge in self.witness + (
            self.dependencies if self.dependencies is not None else ()
        ):
            references.update((edge.source, edge.target))
            for evidence in edge.evidence:
                evidence.validate_sources(sources)
        _require_known_references(references, nodes, "cycle node references")


class ImportFinding(ReportModel):
    kind: Literal["unresolved_import"]
    source: Text
    requested: Text | None
    code: Text
    message: Text
    evidence: tuple[ImportEvidence, ...]
    node: Text | None

    def validate_references(self, nodes: set[str], sources: set[str]) -> None:
        _require_known_references({self.source}, sources, "import source references")
        if self.node is not None:
            _require_known_references({self.node}, nodes, "import node references")
        for evidence in self.evidence:
            evidence.validate_sources(sources)


class RuleFinding(ReportModel):
    kind: Literal["rule"]
    code: Text
    message: Text
    node_ids: tuple[Text, ...]
    source_ids: tuple[Text, ...]
    evidence: tuple[ImportEvidence, ...]

    def validate_references(self, nodes: set[str], sources: set[str]) -> None:
        _require_known_references(set(self.node_ids), nodes, "rule node references")
        _require_known_references(set(self.source_ids), sources, "rule source references")
        for evidence in self.evidence:
            evidence.validate_sources(sources)


Finding = Annotated[
    CycleFinding | ImportFinding | RuleFinding, Field(discriminator="kind")
]


class RegisteredFinding(ReportModel):
    check_id: Text
    severity: Severity
    finding: Finding


class ViewNode(ReportModel):
    id: Text
    label: Text
    members: Annotated[tuple[Text, ...], Field(min_length=1)]


class GraphView(ReportModel):
    nodes: tuple[ViewNode, ...]
    enabled_check_ids: tuple[Text, ...]
    dependency_count: Count
    cyclic_node_count: Count
    cyclic_dependency_count: Count
    findings: tuple[RegisteredFinding, ...]

    def validate_references(self, sources: set[str]) -> None:
        nodes = {node.id for node in self.nodes}
        if len(nodes) != len(self.nodes):
            raise ValueError("view node IDs must be unique")
        members = tuple(member for node in self.nodes for member in node.members)
        if len(set(members)) != len(members):
            raise ValueError("view node memberships must be disjoint")
        _require_known_references(set(members), sources, "view memberships")
        checks = set(self.enabled_check_ids)
        if len(checks) != len(self.enabled_check_ids):
            raise ValueError("enabled check IDs must be unique")
        for registered in self.findings:
            if registered.check_id not in checks:
                raise ValueError("findings must belong to an enabled check")
            registered.finding.validate_references(nodes, sources)


class SourceModule(ReportModel):
    id: Text
    path: Text
    is_package: bool
    parent_package: Text | None
    import_name: Text | None
    binding_status: Literal["bound", "path_only", "shadowed", "ambiguous"]
    analysis_status: Literal["pending", "analyzed", "error"]


class Diagnostic(ReportModel):
    severity: Severity
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
    schema_version: Literal["0.7"]
    status: Literal["complete", "incomplete"]
    gate: Text
    sources: tuple[SourceModule, ...]
    coverage: Coverage
    views: dict[Text, GraphView]

    @model_validator(mode="after")
    def validate_coverage_and_source_references(self) -> Self:
        sources = {source.id for source in self.sources}
        if len(sources) != len(self.sources):
            raise ValueError("source IDs must be unique")
        if self.gate not in self.views:
            raise ValueError("selected gate must name a reported view")
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
        for view in self.views.values():
            view.validate_references(sources)
        for boundary in self.coverage.boundaries:
            for evidence in boundary.evidence:
                evidence.validate_sources(sources)
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
                problem=f"Expected a schema 0.7 JSON report: {error}",
            ) from error
        selected = report.views[report.gate]
        incomplete = report.status == "incomplete"
        has_errors = any(item.severity == "error" for item in selected.findings)
        expected_exit = 2 if incomplete else int(has_errors)
        if exit_code != expected_exit:
            raise CheckOutputError(
                check_id=check_id,
                command=self.name,
                summary="PyArchGraph exit code disagrees with its report",
                problem=(
                    f"Exit {exit_code} with status {report.status!r}, "
                    f"gate {report.gate!r} and error findings {has_errors}; "
                    f"expected exit {expected_exit}."
                ),
            )
        status = (
            CheckStatus.ERROR
            if incomplete
            else CheckStatus.FAILED if has_errors else CheckStatus.PASSED
        )
        summary = (
            f"Analysis is {report.status}; gate: {report.gate}; "
            f"sources: {len(report.sources)}; "
            f"analyzed: {report.coverage.analyzed_source_count}."
        )
        messages = [summary, self._view_message(report.gate, selected, True)]
        for gate in sorted(report.views):
            if gate != report.gate:
                messages.append(
                    self._view_message(gate, report.views[gate], False)
                )
        messages.extend(
            self._diagnostic_message(diagnostic)
            for diagnostic in report.coverage.diagnostics
        )
        messages.extend(
            self._boundary_message(boundary) for boundary in report.coverage.boundaries
        )
        source_labels = {
            source.id: source.import_name
            if source.import_name is not None
            else source.path
            for source in report.sources
        }
        node_labels = {node.id: node.label for node in selected.nodes}
        prefix = "Partial observation: " if incomplete else ""
        messages.extend(
            prefix
            + f"{registered.severity.capitalize()} [{registered.check_id}]: "
            + self._finding_message(registered.finding, node_labels, source_labels)
            for registered in selected.findings
        )
        return CheckResult(check_id=check_id, status=status, messages=tuple(messages))

    def _view_message(self, gate: str, view: GraphView, selected: bool) -> str:
        label = "Selected view" if selected else "Informational view"
        return (
            f"{label} ({gate}): dependencies: {view.dependency_count}; "
            f"cyclic nodes: {view.cyclic_node_count}; "
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

    def _finding_message(
        self,
        finding: Finding,
        nodes: dict[str, str],
        sources: dict[str, str],
    ) -> str:
        if isinstance(finding, ImportFinding):
            message = (
                f"Unresolved import in {sources[finding.source]}: {finding.message} "
                f"[{finding.code}]."
            )
            if finding.evidence:
                message += f" {self._evidence_text(finding.evidence)}"
            return message
        if isinstance(finding, RuleFinding):
            return self._rule_message(finding, nodes, sources)
        edges = (
            finding.witness if finding.dependencies is None else finding.dependencies
        )
        label = "Witness" if finding.dependencies is None else "Component dependencies"
        dependencies = "; ".join(
            f"{nodes[edge.source]} -> {nodes[edge.target]} "
            f"at {self._evidence_text(edge.evidence)}"
            for edge in edges
        )
        if finding.definite_members:
            definite = ", ".join(nodes[member] for member in finding.definite_members)
            summary = f"Definite cyclic nodes: {definite}."
            possible_members = tuple(
                member
                for member in finding.members
                if member not in finding.definite_members
            )
            if possible_members:
                summary += (
                    " Other component nodes with possible cycle involvement: "
                    f"{', '.join(nodes[member] for member in possible_members)}."
                )
        else:
            members = ", ".join(nodes[member] for member in finding.members)
            summary = f"Possible dependency cycle among {members}."
        return f"{summary} {label}: {dependencies}"

    def _rule_message(
        self,
        finding: RuleFinding,
        nodes: dict[str, str],
        sources: dict[str, str],
    ) -> str:
        messages = [f"{finding.message} [{finding.code}]."]
        if finding.node_ids:
            messages.append(
                f"Nodes: {', '.join(nodes[node] for node in finding.node_ids)}."
            )
        if finding.source_ids:
            messages.append(
                f"Sources: {', '.join(sources[source] for source in finding.source_ids)}."
            )
        if finding.evidence:
            messages.append(self._evidence_text(finding.evidence))
        return " ".join(messages)
