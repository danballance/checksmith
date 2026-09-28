from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from checksmith.commands.command import CapturedOutputCommand
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
        _require_known_references(
            set(self.source_ids), sources, "rule source references"
        )
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
    dependencies: tuple[Dependency, ...] | None
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
        if self.dependencies is not None:
            for dependency in self.dependencies:
                _require_known_references(
                    {dependency.source, dependency.target},
                    nodes,
                    "dependency node references",
                )
                for evidence in dependency.evidence:
                    evidence.validate_sources(sources)
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
    schema_version: Literal["0.8"]
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
                raise ValueError(
                    "complete report analyzed source count must match sources"
                )
            if any(item.severity == "error" for item in self.coverage.diagnostics):
                raise ValueError("complete reports cannot contain error diagnostics")
            if any(not item.acknowledged for item in self.coverage.boundaries):
                raise ValueError(
                    "complete reports cannot contain unacknowledged boundaries"
                )
        for view in self.views.values():
            view.validate_references(sources)
        for boundary in self.coverage.boundaries:
            for evidence in boundary.evidence:
                evidence.validate_sources(sources)
        return self


class PyArchGraphCommand(CapturedOutputCommand):
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
                problem=f"Expected a schema 0.8 JSON report: {error}",
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
            else CheckStatus.FAILED
            if has_errors
            else CheckStatus.PASSED
        )
        return CheckResult(
            check_id=check_id, status=status, messages=self._report_messages(report)
        )

    def _report_messages(self, report: PyArchGraphReport) -> tuple[str, ...]:
        selected = report.views[report.gate]
        incomplete = report.status == "incomplete"
        errors = sum(item.severity == "error" for item in selected.findings)
        outcome = (
            "Analysis incomplete; findings are partial."
            if incomplete
            else f"{errors} error finding{'s' if errors != 1 else ''}."
            if errors
            else "No error findings."
        )
        messages = [
            f"{report.gate}: {outcome}",
            (
                f"Selected view: {report.gate} (determines pass/fail)\n"
                f"  {self._view_description(report.gate)}\n"
                f"  Analysis: {report.status}; source files analyzed: "
                f"{report.coverage.analyzed_source_count}/{len(report.sources)}.\n"
                f"  {self._view_counts(selected)}\n"
                f"  Enabled checks: {', '.join(selected.enabled_check_ids) or '(none)'}."
            ),
        ]
        if report.gate in (
            "package-structural",
            "package-non-typing",
            "package-module-body",
        ):
            messages[1] += (
                "\n  Package groups combine modules; cycles between groups can exist "
                "even when individual modules have no cycles."
            )
        source_labels = {
            source.id: source.import_name
            if source.import_name is not None
            else source.path
            for source in report.sources
        }
        node_labels = {node.id: node.label for node in selected.nodes}
        for index, registered in enumerate(selected.findings, start=1):
            prefix = "Partial observation - " if incomplete else ""
            messages.append(
                f"{prefix}Finding {index} of {len(selected.findings)}: "
                f"{registered.severity.capitalize()} [{registered.check_id}]\n"
                + self._finding_message(registered.finding, node_labels, source_labels)
            )
        if report.coverage.diagnostics:
            messages.append(
                "Analysis diagnostics (source discovery and parsing)\n"
                + "\n".join(
                    self._diagnostic_message(item)
                    for item in report.coverage.diagnostics
                )
            )
        if report.coverage.boundaries:
            messages.append(
                "Analysis boundaries\n"
                + "\n".join(
                    self._boundary_message(item) for item in report.coverage.boundaries
                )
            )
        other_views = sorted(set(report.views) - {report.gate})
        if other_views:
            messages.append(
                "Other views (information only; do not affect this result)\n"
                + "\n".join(
                    f"  {gate}: {self._view_counts(report.views[gate])}"
                    for gate in other_views
                )
                + "\n  View key: structural = all explicit imports; "
                "non-typing = excludes typing-only imports; "
                "module-body = also excludes imports inside functions/methods."
                "\n  The package- prefix groups modules by their immediate package; "
                "standalone modules remain individual nodes."
            )
        if report.coverage.limitations:
            messages.append(
                "Analysis limitations\n"
                + "\n".join(f"  - {item}" for item in report.coverage.limitations)
            )
        return tuple(messages)

    def _view_description(self, gate: str) -> str:
        match gate:
            case "structural" | "package-structural":
                scope = (
                    "All explicit imports, including typing-only and function-local."
                )
            case "non-typing" | "package-non-typing":
                scope = "Excludes typing-only imports; includes function-local imports."
            case "module-body" | "package-module-body":
                scope = "Excludes typing-only imports and imports in functions/methods."
            case _:
                return (
                    "Custom graph view; nodes and checks are defined by the producer."
                )
        grouping = (
            "Grouped by immediate package; standalone modules remain separate."
            if gate.startswith("package-")
            else "Each node represents a source module."
        )
        return f"{grouping}\n  {scope}"

    def _view_counts(self, view: GraphView) -> str:
        severities = ", ".join(
            f"{label}: {count}"
            for severity, label in (
                ("error", "errors"),
                ("warning", "warnings"),
                ("info", "info"),
            )
            if (count := sum(item.severity == severity for item in view.findings))
        )
        return (
            f"{view.dependency_count} "
            f"{'dependency' if view.dependency_count == 1 else 'dependencies'}; "
            f"{view.cyclic_node_count} "
            f"node{'s' if view.cyclic_node_count != 1 else ''} in cycles; "
            f"{len(view.findings)} finding{'s' if len(view.findings) != 1 else ''}"
            + (f" ({severities})" if severities else "")
            + "."
        )

    def _diagnostic_message(self, diagnostic: Diagnostic) -> str:
        location = diagnostic.path or ""
        if diagnostic.line is not None:
            location += f":{diagnostic.line}"
        if diagnostic.column is not None:
            location += f":{diagnostic.column}"
        return (
            f"  {diagnostic.severity.capitalize()} [{diagnostic.code}]\n"
            + (f"    Location: {location}\n" if location else "")
            + f"    {diagnostic.message}"
        )

    def _boundary_message(self, boundary: TargetBoundary) -> str:
        accepted = "Acknowledged" if boundary.acknowledged else "Unacknowledged"
        location = f" ({boundary.path})" if boundary.path is not None else ""
        evidence = (
            f"\n{self._evidence_text(boundary.evidence)}" if boundary.evidence else ""
        )
        return (
            f"  {accepted} {boundary.kind} boundary {boundary.name}{location}\n"
            f"    {boundary.reason}{evidence}"
        )

    def _evidence_text(self, evidence: tuple[ImportEvidence, ...]) -> str:
        locations: list[str] = []
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
            locations.append(f"      {item.path}:{item.line}:{item.column}")
            if item.source_segment:
                locations.extend(
                    f"        {line}" for line in item.source_segment.splitlines()
                )
            if labels:
                locations.append(f"        Context: {', '.join(labels)}")
        return "\n".join(locations)

    def _finding_message(
        self,
        finding: Finding,
        nodes: dict[str, str],
        sources: dict[str, str],
    ) -> str:
        if isinstance(finding, ImportFinding):
            message = (
                f"  Unresolved import in {sources[finding.source]}\n"
                f"  {finding.message} [{finding.code}]"
            )
            if finding.requested is not None:
                message += f"\n  Requested import: {finding.requested}"
            if finding.evidence:
                message += (
                    f"\n  Import locations:\n{self._evidence_text(finding.evidence)}"
                )
            return message
        if isinstance(finding, RuleFinding):
            return self._rule_message(finding, nodes, sources)
        lines = [f"  Cycle group ({finding.certainty})"]
        if finding.definite_members:
            lines.append("  Confirmed cyclic nodes:")
            lines.extend(
                f"    - {nodes[member]}" for member in finding.definite_members
            )
        possible_members = tuple(
            member
            for member in finding.members
            if member not in finding.definite_members
        )
        if possible_members:
            lines.append("  Nodes with possible cycle involvement:")
            lines.extend(f"    - {nodes[member]}" for member in possible_members)
        lines.append(f"  Dependencies in this group: {finding.dependency_count}.")
        if finding.dependencies is None:
            edges = finding.witness
            lines.append("  Example cycle (witness; may cover only part of the group):")
        else:
            edges = finding.dependencies
            lines.append("  All dependencies in this group:")
        for index, edge in enumerate(edges, start=1):
            lines.append(f"    {index}. {nodes[edge.source]} -> {nodes[edge.target]}")
            lines.append(self._evidence_text(edge.evidence))
        return "\n".join(lines)

    def _rule_message(
        self,
        finding: RuleFinding,
        nodes: dict[str, str],
        sources: dict[str, str],
    ) -> str:
        messages = [f"  {finding.message} [{finding.code}]"]
        if finding.node_ids:
            messages.append("  Nodes:")
            messages.extend(f"    - {nodes[node]}" for node in finding.node_ids)
        if finding.source_ids:
            messages.append("  Sources:")
            messages.extend(f"    - {sources[source]}" for source in finding.source_ids)
        if finding.evidence:
            messages.append("  Import locations:")
            messages.append(self._evidence_text(finding.evidence))
        return "\n".join(messages)
