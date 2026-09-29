import json
from pathlib import Path
from typing import Literal

import pytest
from pydantic import JsonValue

from checksmith.adapters.driven.execution.command import CapturedOutputCommand
from checksmith.adapters.driven.execution.packages import build_check_argv
from checksmith.adapters.driven.execution.pyarchgraph import (
    PyArchGraphCommand,
    PyArchGraphReport,
)
from checksmith.adapters.driven.execution.registry import CommandFactory
from checksmith.domain.config import Check
from checksmith.domain.errors import CheckOutputError
from checksmith.domain.models import CheckStatus, CommandName, PackageType
from tests.conftest import FakeProcesses


def evidence() -> dict[str, JsonValue]:
    return {
        "path": "src/app.py",
        "line": 3,
        "column": 1,
        "source_segment": "import service",
        "resolution_kind": "exact_module",
        "source": "app",
        "target": "service",
        "fact_id": "app-import-service",
        "context": {
            "scope": "module",
            "in_function": False,
            "typing_only": False,
            "conditional": False,
            "exception_handler": False,
            "package_initializer": False,
        },
    }


def dependency() -> dict[str, JsonValue]:
    return {"source": "app", "target": "service", "evidence": [evidence()]}


def cycle_finding(certainty: Literal["definite", "possible"]) -> dict[str, JsonValue]:
    return {
        "kind": "cycle",
        "certainty": certainty,
        "members": ["app", "service"],
        "definite_members": ["app", "service"] if certainty == "definite" else [],
        "dependency_count": 2,
        "dependencies": None,
        "witness": [
            dependency(),
            {
                "source": "service",
                "target": "app",
                "evidence": [
                    {
                        **evidence(),
                        "path": "src/service.py",
                        "source_segment": "import app",
                        "source": "service",
                        "target": "app",
                        "fact_id": "service-import-app",
                    }
                ],
            },
        ],
    }


def import_finding() -> dict[str, JsonValue]:
    return {
        "kind": "unresolved_import",
        "source": "app",
        "node": "app",
        "requested": None,
        "code": "missing_internal_target",
        "message": "Import needs review",
        "evidence": [
            {
                **evidence(),
                "resolution_kind": None,
                "source_segment": None,
                "target": None,
            }
        ],
    }


def registered_finding(
    *, finding: JsonValue, check_id: str, severity: Literal["error", "warning", "info"]
) -> dict[str, JsonValue]:
    return {"check_id": check_id, "severity": severity, "finding": finding}


def report_json(*, findings: list[JsonValue]) -> str:
    view = {
        "nodes": [
            {"id": name, "label": name, "members": [name]}
            for name in ("app", "service")
        ],
        "enabled_check_ids": ["cycles", "unresolved-imports"],
        "dependency_count": 2,
        "dependencies": None,
        "cyclic_node_count": 2 if findings else 0,
        "cyclic_dependency_count": 2 if findings else 0,
        "findings": [
            registered_finding(
                finding=finding,
                check_id=(
                    "unresolved-imports"
                    if isinstance(finding, dict)
                    and finding.get("kind") == "unresolved_import"
                    else "cycles"
                ),
                severity="error",
            )
            for finding in findings
        ],
    }
    return json.dumps(
        {
            "schema_version": "0.8",
            "status": "complete",
            "gate": "structural",
            "sources": [
                {
                    "id": name,
                    "path": f"src/{name}.py",
                    "is_package": False,
                    "parent_package": None,
                    "import_name": name,
                    "binding_status": "bound",
                    "analysis_status": "analyzed",
                }
                for name in ("app", "service")
            ],
            "coverage": {
                "roots": ["src"],
                "excludes": ["tests"],
                "excluded_paths": [],
                "analyzed_source_count": 2,
                "diagnostics": [],
                "boundaries": [],
                "limitations": ["Only explicit import statements are analyzed."],
            },
            "views": {"structural": view, "non-typing": view, "module-body": view},
        }
    )


HEALTHY_REPORT = report_json(findings=[])
HEALTHY_MESSAGES = (
    "structural: No error findings.",
    (
        "Selected view: structural (determines pass/fail)\n"
        "  Each node represents a source module.\n"
        "  All explicit imports, including typing-only and function-local.\n"
        "  Analysis: complete; source files analyzed: 2/2.\n"
        "  2 dependencies; 0 nodes in cycles; 0 findings.\n"
        "  Enabled checks: cycles, unresolved-imports."
    ),
    (
        "Other views (information only; do not affect this result)\n"
        "  module-body: 2 dependencies; 0 nodes in cycles; 0 findings.\n"
        "  non-typing: 2 dependencies; 0 nodes in cycles; 0 findings.\n"
        "  View key: structural = all explicit imports; "
        "non-typing = excludes typing-only imports; "
        "module-body = also excludes imports inside functions/methods.\n"
        "  The package- prefix groups modules by their immediate package; "
        "standalone modules remain individual nodes."
    ),
    "Analysis limitations\n  - Only explicit import statements are analyzed.",
)


def report_section(messages: tuple[str, ...], prefix: str) -> str:
    sections = tuple(message for message in messages if message.startswith(prefix))
    assert len(sections) == 1
    return sections[0]


def custom_report_json(*, severity: Literal["error", "warning", "info"]) -> str:
    payload = json.loads(HEALTHY_REPORT)
    payload["gate"] = "packages"
    payload["views"] = {
        "packages": {
            "nodes": [
                {
                    "id": "application",
                    "label": "Application package",
                    "members": ["app", "service"],
                }
            ],
            "enabled_check_ids": ["group-size"],
            "dependency_count": 0,
            "dependencies": None,
            "cyclic_dependency_count": 0,
            "cyclic_node_count": 0,
            "findings": [
                registered_finding(
                    check_id="group-size",
                    severity=severity,
                    finding={
                        "kind": "rule",
                        "code": "large-group",
                        "message": "Consider a smaller package",
                        "node_ids": ["application"],
                        "source_ids": ["app"],
                        "evidence": [evidence()],
                    },
                )
            ],
        }
    }
    return json.dumps(payload)


def partial_report_json(*, findings: list[JsonValue]) -> str:
    payload = json.loads(report_json(findings=findings))
    payload["status"] = "incomplete"
    payload["coverage"]["analyzed_source_count"] = 1
    payload["sources"][1]["analysis_status"] = "error"
    payload["coverage"]["diagnostics"] = [
        {
            "severity": "error",
            "code": "source_syntax_error",
            "message": "Python source could not be parsed.",
            "path": "src/broken.py",
            "line": 4,
            "column": 2,
        }
    ]
    return json.dumps(payload)


def test_command_uses_normal_subprocess_execution(
    tmp_path: Path,
    processes: FakeProcesses,
    command_factory: CommandFactory,
) -> None:
    check = Check(
        id="architecture",
        package_type=PackageType.UVX,
        package="pyarchgraph @ git+https://github.com/danballance/pyarchgraph@main",
        command=CommandName.PYARCHGRAPH,
        args=("src", "plugins", "--exclude", "vendor/*", "--gate", "module-body"),
    )
    processes.stdout = HEALTHY_REPORT

    result = command_factory.for_name(name=CommandName.PYARCHGRAPH).run(
        check=check, project_root=tmp_path, output=None
    )

    assert PyArchGraphCommand.run is CapturedOutputCommand.run
    assert result.status is CheckStatus.PASSED
    assert result.messages == HEALTHY_MESSAGES
    assert processes.started[0].argv == build_check_argv(check)
    assert processes.started[0].cwd == tmp_path
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    ("finding", "message"),
    [
        (
            cycle_finding("definite"),
            "Confirmed cyclic nodes:\n    - app\n    - service",
        ),
        (
            cycle_finding("possible"),
            "Nodes with possible cycle involvement:\n    - app\n    - service",
        ),
        (
            import_finding(),
            "Unresolved import in app\n  Import needs review",
        ),
    ],
)
def test_each_finding_fails_with_actionable_locations(
    finding: JsonValue,
    message: str,
    tmp_path: Path,
    command_factory: CommandFactory,
) -> None:
    result = command_factory.for_name(name=CommandName.PYARCHGRAPH).process_response(
        check_id="architecture",
        project_root=tmp_path,
        exit_code=1,
        stdout=report_json(findings=[finding]),
        stderr="",
    )

    assert result.status is CheckStatus.FAILED
    assert result.check_id == "architecture"
    assert len(result.messages) == 5
    finding_message = report_section(result.messages, "Finding 1 of 1:")
    assert message in finding_message
    assert "src/app.py:3:1" in finding_message


def test_all_evidence_and_findings_are_reported(
    tmp_path: Path, command_factory: CommandFactory
) -> None:
    payload = json.loads(
        report_json(findings=[cycle_finding("definite"), import_finding()])
    )
    cycle_finding_payload = payload["views"]["structural"]["findings"][0]["finding"]
    cycle_finding_payload["witness"][0]["evidence"].append(
        {**evidence(), "line": 12, "source_segment": "from service import handler"}
    )
    result = command_factory.for_name(name=CommandName.PYARCHGRAPH).process_response(
        check_id="architecture",
        project_root=tmp_path,
        exit_code=1,
        stdout=json.dumps(payload),
        stderr="",
    )

    assert len(result.messages) == 6
    cycle = report_section(result.messages, "Finding 1 of 2: Error [cycles]")
    unresolved = report_section(
        result.messages, "Finding 2 of 2: Error [unresolved-imports]"
    )
    assert (
        "    1. app -> service\n      src/app.py:3:1\n        import service" in cycle
    )
    assert "      src/app.py:12:1\n        from service import handler" in cycle
    assert (
        "    2. service -> app\n      src/service.py:3:1\n        import app" in cycle
    )
    assert cycle.index("src/app.py:12:1") < cycle.index("2. service -> app")
    assert "Unresolved import in app" in unresolved
    assert "  Import locations:\n      src/app.py:3:1" in unresolved


@pytest.mark.parametrize("exit_code", [-9, 3, 127])
def test_analysis_and_unexpected_exits_are_errors(
    exit_code: int,
    tmp_path: Path,
    command_factory: CommandFactory,
) -> None:
    with pytest.raises(CheckOutputError, match=f"exited {exit_code}") as caught:
        command_factory.for_name(name=CommandName.PYARCHGRAPH).process_response(
            check_id="architecture",
            project_root=tmp_path,
            exit_code=exit_code,
            stdout="partial output",
            stderr="Cannot analyse source",
        )
    assert "Cannot analyse source" in str(caught.value)
    assert "partial output" in str(caught.value)


@pytest.mark.parametrize(
    ("exit_code", "findings"),
    [(0, [cycle_finding("definite")]), (1, [])],
)
def test_exit_must_agree_with_findings(
    exit_code: int,
    findings: list[JsonValue],
    tmp_path: Path,
    command_factory: CommandFactory,
) -> None:
    with pytest.raises(CheckOutputError, match="exit code disagrees"):
        command_factory.for_name(name=CommandName.PYARCHGRAPH).process_response(
            check_id="architecture",
            project_root=tmp_path,
            exit_code=exit_code,
            stdout=report_json(findings=findings),
            stderr="",
        )


@pytest.mark.parametrize(
    "stdout", ["", "not json", "[]", "{}", HEALTHY_REPORT + "noise"]
)
def test_stdout_must_be_a_complete_json_report(
    stdout: str, tmp_path: Path, command_factory: CommandFactory
) -> None:
    with pytest.raises(CheckOutputError, match="Invalid PyArchGraph report on stdout"):
        command_factory.for_name(name=CommandName.PYARCHGRAPH).process_response(
            check_id="architecture",
            project_root=tmp_path,
            exit_code=0,
            stdout=stdout,
            stderr="",
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", "0.6"),
        ("schema_version", "0.7"),
        ("status", "partial"),
        ("gate", "non_typing"),
        ("sources", {}),
        ("views", {}),
        ("coverage", None),
        ("unexpected", "extra"),
    ],
)
def test_report_schema_is_strict(
    field: str, value: JsonValue, tmp_path: Path, command_factory: CommandFactory
) -> None:
    payload = json.loads(HEALTHY_REPORT)
    payload[field] = value
    with pytest.raises(CheckOutputError, match="schema 0.8"):
        command_factory.for_name(name=CommandName.PYARCHGRAPH).process_response(
            check_id="architecture",
            project_root=tmp_path,
            exit_code=0,
            stdout=json.dumps(payload),
            stderr="",
        )


@pytest.mark.parametrize(
    "finding",
    [
        {"kind": "unknown"},
        {**cycle_finding("definite"), "certainty": "assumed"},
        {**cycle_finding("definite"), "witness": []},
        {**cycle_finding("definite"), "members": []},
        {**cycle_finding("definite"), "dependency_count": 0},
        {**cycle_finding("definite"), "dependencies": []},
        {**cycle_finding("definite"), "kind": "forbidden_dependency"},
        {**import_finding(), "kind": "dynamic_import"},
        {**import_finding(), "evidence": [{**evidence(), "line": 0}]},
        {
            **import_finding(),
            "evidence": [{**evidence(), "column": True}],
        },
        {**import_finding(), "evidence": [{**evidence(), "path": ""}]},
        {
            **import_finding(),
            "evidence": [{**evidence(), "resolution_kind": "unknown"}],
        },
        {
            **import_finding(),
            "evidence": [{**evidence(), "resolution_kind": "dynamic_literal"}],
        },
    ],
)
def test_malformed_findings_are_errors(
    finding: JsonValue, tmp_path: Path, command_factory: CommandFactory
) -> None:
    with pytest.raises(CheckOutputError, match="schema 0.8"):
        command_factory.for_name(name=CommandName.PYARCHGRAPH).process_response(
            check_id="architecture",
            project_root=tmp_path,
            exit_code=1,
            stdout=report_json(findings=[finding]),
            stderr="",
        )


@pytest.mark.parametrize(
    ("severity", "exit_code", "status"),
    [("warning", 0, CheckStatus.PASSED), ("error", 1, CheckStatus.FAILED)],
)
def test_import_findings_can_omit_evidence(
    severity: str,
    exit_code: int,
    status: CheckStatus,
    tmp_path: Path,
    command_factory: CommandFactory,
) -> None:
    payload = json.loads(report_json(findings=[{**import_finding(), "evidence": []}]))
    for view in payload["views"].values():
        view["findings"][0]["severity"] = severity

    result = command_factory.for_name(name=CommandName.PYARCHGRAPH).process_response(
        check_id="architecture",
        project_root=tmp_path,
        exit_code=exit_code,
        stdout=json.dumps(payload),
        stderr="",
    )

    assert result.status is status
    assert report_section(result.messages, "Finding 1 of 1:") == (
        f"Finding 1 of 1: {severity.capitalize()} [unresolved-imports]\n"
        "  Unresolved import in app\n"
        "  Import needs review [missing_internal_target]"
    )


def test_mixed_component_does_not_overstate_possible_members(
    tmp_path: Path, command_factory: CommandFactory
) -> None:
    finding = {
        **cycle_finding("definite"),
        "members": ["app", "plugin", "service"],
    }
    payload = json.loads(report_json(findings=[finding]))
    payload["sources"].append(
        {**payload["sources"][0], "id": "plugin", "import_name": "plugin"}
    )
    payload["coverage"]["analyzed_source_count"] = 3
    for view in payload["views"].values():
        view["nodes"].append({"id": "plugin", "label": "plugin", "members": ["plugin"]})
    result = command_factory.for_name(name=CommandName.PYARCHGRAPH).process_response(
        check_id="architecture",
        project_root=tmp_path,
        exit_code=1,
        stdout=json.dumps(payload),
        stderr="",
    )

    assert result.status is CheckStatus.FAILED
    message = report_section(result.messages, "Finding 1 of 1: Error [cycles]")
    assert "Confirmed cyclic nodes:\n    - app\n    - service" in message
    assert "Nodes with possible cycle involvement:\n    - plugin" in message
    assert "Example cycle (witness; may cover only part of the group):" in message
    assert "1. app -> service\n      src/app.py:3:1" in message
    assert "2. service -> app\n      src/service.py:3:1" in message


@pytest.mark.parametrize(
    ("gate", "status"),
    [
        ("structural", CheckStatus.FAILED),
        ("non-typing", CheckStatus.FAILED),
        ("module-body", CheckStatus.PASSED),
    ],
)
def test_only_selected_view_controls_gate(
    gate: str,
    status: CheckStatus,
    tmp_path: Path,
    command_factory: CommandFactory,
) -> None:
    payload = json.loads(report_json(findings=[cycle_finding("definite")]))
    payload["gate"] = gate
    payload["views"]["module-body"] = {
        "nodes": payload["views"]["module-body"]["nodes"],
        "enabled_check_ids": ["cycles", "unresolved-imports"],
        "dependency_count": 0,
        "dependencies": None,
        "cyclic_node_count": 0,
        "cyclic_dependency_count": 0,
        "findings": [],
    }
    result = command_factory.for_name(name=CommandName.PYARCHGRAPH).process_response(
        check_id="architecture",
        project_root=tmp_path,
        exit_code=int(status is CheckStatus.FAILED),
        stdout=json.dumps(payload),
        stderr="",
    )
    assert result.status is status
    assert result.messages[0].startswith(f"{gate}:")
    selected = report_section(result.messages, "Selected view:")
    assert selected.startswith(f"Selected view: {gate} (determines pass/fail)")
    other_views = report_section(result.messages, "Other views")
    assert "information only; do not affect this result" in other_views
    assert f"\n  {gate}:" not in other_views
    if status is CheckStatus.PASSED:
        assert result.messages[0] == "module-body: No error findings."
        assert "0 dependencies; 0 nodes in cycles; 0 findings." in selected
        for alternate in ("non-typing", "structural"):
            assert (
                f"  {alternate}: 2 dependencies; 2 nodes in cycles; 1 finding "
                "(errors: 1)."
            ) in other_views
        assert not any(message.startswith("Finding") for message in result.messages)
    else:
        assert "2 dependencies; 2 nodes in cycles; 1 finding" in selected
        assert (
            "module-body: 0 dependencies; 0 nodes in cycles; 0 findings." in other_views
        )


@pytest.mark.parametrize("findings", [[], [cycle_finding("definite")]])
def test_partial_report_preserves_findings_and_coverage_as_error(
    findings: list[JsonValue], tmp_path: Path, command_factory: CommandFactory
) -> None:
    result = command_factory.for_name(name=CommandName.PYARCHGRAPH).process_response(
        check_id="architecture",
        project_root=tmp_path,
        exit_code=2,
        stdout=partial_report_json(findings=findings),
        stderr="",
    )
    assert result.status is CheckStatus.ERROR
    assert "analyzed: 1/2" in report_section(result.messages, "Selected view:")
    assert (
        result.messages[0] == "structural: Analysis incomplete; findings are partial."
    )
    diagnostics = report_section(result.messages, "Analysis diagnostics")
    assert (
        "  Error [source_syntax_error]\n    Location: src/broken.py:4:2" in diagnostics
    )
    assert any(
        "Example cycle (witness;" in message for message in result.messages
    ) == bool(findings)
    assert len(result.messages) == 5 + len(findings)
    if findings:
        observation = report_section(result.messages, "Partial observation - Finding")
        assert observation.startswith(
            "Partial observation - Finding 1 of 1: Error [cycles]"
        )
        assert "Confirmed cyclic nodes:" in observation


@pytest.mark.parametrize(
    ("stdout", "exit_code"),
    [
        (partial_report_json(findings=[]), 0),
        (partial_report_json(findings=[cycle_finding("definite")]), 1),
        (HEALTHY_REPORT, 2),
    ],
)
def test_completion_status_must_agree_with_exit(
    stdout: str,
    exit_code: int,
    tmp_path: Path,
    command_factory: CommandFactory,
) -> None:
    with pytest.raises(CheckOutputError, match="exit code disagrees"):
        command_factory.for_name(name=CommandName.PYARCHGRAPH).process_response(
            check_id="architecture",
            project_root=tmp_path,
            exit_code=exit_code,
            stdout=stdout,
            stderr="",
        )


def test_exit_two_without_report_preserves_process_error(
    tmp_path: Path, command_factory: CommandFactory
) -> None:
    with pytest.raises(CheckOutputError, match="Cannot read configuration"):
        command_factory.for_name(name=CommandName.PYARCHGRAPH).process_response(
            check_id="architecture",
            project_root=tmp_path,
            exit_code=2,
            stdout="",
            stderr="Cannot read configuration",
        )


@pytest.mark.parametrize("acknowledged", [False, True])
def test_boundaries_remain_visible_after_acknowledgement(
    acknowledged: bool, tmp_path: Path, command_factory: CommandFactory
) -> None:
    payload = json.loads(HEALTHY_REPORT)
    payload["status"] = "complete" if acknowledged else "incomplete"
    payload["coverage"]["boundaries"] = [
        {
            "name": "pkg.native",
            "kind": "native",
            "path": "src/pkg/native.pyx",
            "reason": "Native implementation is outside source analysis",
            "acknowledged": acknowledged,
            "evidence": [evidence()],
        },
        {
            "name": "pkg.generated",
            "kind": "generated",
            "path": None,
            "reason": "Generated at build time",
            "acknowledged": True,
            "evidence": [],
        },
    ]
    payload["coverage"]["diagnostics"] = [
        {
            "severity": "info",
            "code": "declared_boundary",
            "message": "A generated target was declared.",
            "path": None,
            "line": None,
            "column": None,
        }
    ]
    result = command_factory.for_name(name=CommandName.PYARCHGRAPH).process_response(
        check_id="architecture",
        project_root=tmp_path,
        exit_code=0 if acknowledged else 2,
        stdout=json.dumps(payload),
        stderr="",
    )
    assert result.status is (CheckStatus.PASSED if acknowledged else CheckStatus.ERROR)
    messages = "\n".join(result.messages)
    assert "Info [declared_boundary]\n    A generated target was declared." in messages
    assert "native boundary pkg.native (src/pkg/native.pyx)" in messages
    assert "src/app.py:3:1" in messages
    assert "Acknowledged generated boundary pkg.generated" in messages
    assert ("Unacknowledged" in messages) is not acknowledged


def test_context_and_full_component_details_are_accepted(
    tmp_path: Path, command_factory: CommandFactory
) -> None:
    contextual_evidence = {
        **evidence(),
        "context": {
            "scope": "class",
            "in_function": True,
            "typing_only": True,
            "conditional": True,
            "exception_handler": True,
            "package_initializer": True,
        },
    }
    detailed_edge = {**dependency(), "evidence": [contextual_evidence]}
    return_edge = {**dependency(), "source": "service", "target": "app"}
    self_edge = {**dependency(), "target": "app"}
    finding = {
        **cycle_finding("definite"),
        "witness": [detailed_edge, return_edge],
        "dependency_count": 3,
        "dependencies": [detailed_edge, return_edge, self_edge],
    }
    result = command_factory.for_name(name=CommandName.PYARCHGRAPH).process_response(
        check_id="architecture",
        project_root=tmp_path,
        exit_code=1,
        stdout=report_json(findings=[finding]),
        stderr="",
    )
    message = report_section(result.messages, "Finding 1 of 1:")
    assert "typing-only, function-local, class body" in message
    assert "conditional, exception handler, package initializer" in message
    assert "All dependencies in this group:\n    1. app -> service" in message
    assert "3. app -> app" in message
    assert "Example cycle" not in message


@pytest.mark.parametrize(
    ("section", "field", "value"),
    [
        ("coverage", "analyzed_source_count", True),
        ("coverage", "analyzed_source_count", "2"),
        ("coverage", "analyzed_source_count", -1),
        ("coverage", "unexpected", 1),
        ("module-body", "dependency_count", 1.5),
        ("module-body", "cyclic_node_count", -1),
        ("non-typing", "findings", {}),
        ("structural", "unexpected", None),
    ],
)
def test_nested_schema_and_unselected_views_are_strict(
    section: str,
    field: str,
    value: JsonValue,
    tmp_path: Path,
    command_factory: CommandFactory,
) -> None:
    payload = json.loads(HEALTHY_REPORT)
    target = payload["coverage"] if section == "coverage" else payload["views"][section]
    target[field] = value
    with pytest.raises(CheckOutputError, match="schema 0.8"):
        command_factory.for_name(name=CommandName.PYARCHGRAPH).process_response(
            check_id="architecture",
            project_root=tmp_path,
            exit_code=0,
            stdout=json.dumps(payload),
            stderr="",
        )


def test_partial_report_can_have_no_sources() -> None:
    payload = json.loads(partial_report_json(findings=[]))
    payload["sources"] = []
    payload["coverage"]["analyzed_source_count"] = 0
    for view in payload["views"].values():
        view["nodes"] = []
        view["dependency_count"] = 0
    report = PyArchGraphReport.model_validate_json(json.dumps(payload))
    assert report.sources == ()
    assert report.status == "incomplete"


@pytest.mark.parametrize("source_status", ["error", "pending"])
def test_complete_report_rejects_unanalyzed_sources(
    source_status: str, tmp_path: Path, command_factory: CommandFactory
) -> None:
    payload = json.loads(HEALTHY_REPORT)
    payload["sources"][0]["analysis_status"] = source_status
    with pytest.raises(
        CheckOutputError, match="complete reports must contain only analyzed sources"
    ):
        command_factory.for_name(name=CommandName.PYARCHGRAPH).process_response(
            check_id="architecture",
            project_root=tmp_path,
            exit_code=0,
            stdout=json.dumps(payload),
            stderr="",
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("analyzed_source_count", 1, "analyzed source count must match sources"),
        (
            "diagnostics",
            [
                {
                    "severity": "error",
                    "code": "source_read_error",
                    "message": "Python source could not be read.",
                    "path": "src/app.py",
                    "line": None,
                    "column": None,
                }
            ],
            "complete reports cannot contain error diagnostics",
        ),
        (
            "boundaries",
            [
                {
                    "name": "pkg.native",
                    "kind": "native",
                    "path": None,
                    "reason": "Native implementation is not analyzed",
                    "acknowledged": False,
                    "evidence": [],
                }
            ],
            "complete reports cannot contain unacknowledged boundaries",
        ),
    ],
)
def test_complete_report_rejects_contradictory_coverage(
    field: str,
    value: JsonValue,
    message: str,
    tmp_path: Path,
    command_factory: CommandFactory,
) -> None:
    payload = json.loads(HEALTHY_REPORT)
    payload["coverage"][field] = value
    with pytest.raises(CheckOutputError, match=message):
        command_factory.for_name(name=CommandName.PYARCHGRAPH).process_response(
            check_id="architecture",
            project_root=tmp_path,
            exit_code=0,
            stdout=json.dumps(payload),
            stderr="",
        )


def test_human_findings_use_import_names_or_paths(
    tmp_path: Path, command_factory: CommandFactory
) -> None:
    payload = json.loads(
        partial_report_json(findings=[cycle_finding("definite"), import_finding()])
    )
    payload["sources"][0]["import_name"] = "application.entry"
    payload["sources"][1]["import_name"] = None
    for view in payload["views"].values():
        view["nodes"][0]["label"] = "application.entry"
        view["nodes"][1]["label"] = "src/service.py"
    payload["coverage"]["boundaries"] = [
        {
            "name": "pkg.native",
            "kind": "native",
            "path": None,
            "reason": "Native implementation is not analyzed",
            "acknowledged": False,
            "evidence": [],
        }
    ]
    result = command_factory.for_name(name=CommandName.PYARCHGRAPH).process_response(
        check_id="architecture",
        project_root=tmp_path,
        exit_code=2,
        stdout=json.dumps(payload),
        stderr="",
    )
    boundaries = report_section(result.messages, "Analysis boundaries")
    cycle = report_section(result.messages, "Partial observation - Finding 1 of 2:")
    unresolved = report_section(
        result.messages, "Partial observation - Finding 2 of 2:"
    )
    assert "Unacknowledged native boundary pkg.native" in boundaries
    assert (
        "Confirmed cyclic nodes:\n    - application.entry\n    - src/service.py"
        in cycle
    )
    assert "application.entry -> src/service.py" in cycle
    assert "src/service.py -> application.entry" in cycle
    assert unresolved.startswith(
        "Partial observation - Finding 2 of 2: Error [unresolved-imports]\n"
        "  Unresolved import in application.entry\n"
    )
    report = PyArchGraphReport.model_validate_json(json.dumps(payload))
    assert report.sources[0].id == "app"


@pytest.mark.parametrize("problem", ["duplicate", "unknown", "empty"])
def test_source_identity_protocol_errors_are_rejected(
    problem: str, tmp_path: Path, command_factory: CommandFactory
) -> None:
    payload = json.loads(HEALTHY_REPORT)
    if problem == "duplicate":
        payload["sources"][1]["id"] = "app"
    elif problem == "unknown":
        payload["views"]["module-body"]["findings"] = [
            registered_finding(
                finding={**import_finding(), "source": "uninventoried-source"},
                check_id="unresolved-imports",
                severity="error",
            )
        ]
    else:
        payload["sources"] = []
        payload["coverage"]["analyzed_source_count"] = 0
    with pytest.raises(CheckOutputError, match="schema 0.8"):
        command_factory.for_name(name=CommandName.PYARCHGRAPH).process_response(
            check_id="architecture",
            project_root=tmp_path,
            exit_code=0,
            stdout=json.dumps(payload),
            stderr="",
        )


def test_schema_08_exposes_nodes_checks_and_evidence_provenance() -> None:
    report = PyArchGraphReport.model_validate_json(
        report_json(findings=[cycle_finding("definite"), import_finding()])
    )

    assert report.schema_version == "0.8"
    assert set(report.views) == {"structural", "non-typing", "module-body"}
    view = report.views["structural"]
    assert view.nodes[0].id == "app"
    assert view.nodes[0].label == "app"
    assert view.nodes[0].members == ("app",)
    assert view.enabled_check_ids == ("cycles", "unresolved-imports")
    assert view.cyclic_node_count == 2
    assert view.findings[0].check_id == "cycles"
    assert view.findings[0].severity == "error"
    document = report.model_dump(mode="json")
    cycle = document["views"]["structural"]["findings"][0]["finding"]
    location = cycle["witness"][0]["evidence"][0]
    assert location["source"] == "app"
    assert location["target"] == "service"
    assert location["fact_id"] == "app-import-service"
    assert document["views"]["structural"]["findings"][1]["finding"]["node"] == "app"


@pytest.mark.parametrize(
    ("severity", "exit_code", "expected"),
    [
        ("info", 0, CheckStatus.PASSED),
        ("warning", 0, CheckStatus.PASSED),
        ("error", 1, CheckStatus.FAILED),
    ],
)
def test_custom_views_and_rules_follow_registered_severity(
    severity: Literal["error", "warning", "info"],
    exit_code: int,
    expected: CheckStatus,
    tmp_path: Path,
    command_factory: CommandFactory,
) -> None:
    result = command_factory.for_name(name=CommandName.PYARCHGRAPH).process_response(
        check_id="architecture",
        project_root=tmp_path,
        exit_code=exit_code,
        stdout=custom_report_json(severity=severity),
        stderr="",
    )

    assert result.status is expected
    assert len(result.messages) == 4
    assert result.messages[0] == (
        "packages: 1 error finding."
        if severity == "error"
        else "packages: No error findings."
    )
    selected = report_section(result.messages, "Selected view:")
    assert selected.startswith("Selected view: packages (determines pass/fail)")
    assert (
        "Custom graph view; nodes and checks are defined by the producer." in selected
    )
    message = report_section(result.messages, "Finding 1 of 1:")
    assert message.startswith(
        f"Finding 1 of 1: {severity.capitalize()} [group-size]\n"
        "  Consider a smaller package [large-group]"
    )
    assert "Nodes:\n    - Application package" in message
    assert "Sources:\n    - app" in message
    assert "src/app.py:3:1" in message


@pytest.mark.parametrize("severity", ["warning", "info"])
def test_nonerror_cycles_remain_visible_without_failing(
    severity: str,
    tmp_path: Path,
    command_factory: CommandFactory,
) -> None:
    payload = json.loads(report_json(findings=[cycle_finding("definite")]))
    for view in payload["views"].values():
        view["findings"][0]["severity"] = severity
    result = command_factory.for_name(name=CommandName.PYARCHGRAPH).process_response(
        check_id="architecture",
        project_root=tmp_path,
        exit_code=0,
        stdout=json.dumps(payload),
        stderr="",
    )

    assert result.status is CheckStatus.PASSED
    assert result.messages[0] == "structural: No error findings."
    message = report_section(result.messages, "Finding 1 of 1:")
    assert message.startswith(
        f"Finding 1 of 1: {severity.capitalize()} [cycles]\n  Cycle group (definite)"
    )
    assert "Confirmed cyclic nodes:\n    - app\n    - service" in message


def test_mixed_severities_are_labelled_separately_from_the_error_total(
    tmp_path: Path, command_factory: CommandFactory
) -> None:
    payload = json.loads(report_json(findings=[]))
    payload["views"]["structural"]["findings"] = [
        registered_finding(
            finding=import_finding(),
            check_id="unresolved-imports",
            severity=severity,
        )
        for severity in ("error", "warning", "info")
    ]
    result = command_factory.for_name(name=CommandName.PYARCHGRAPH).process_response(
        check_id="architecture",
        project_root=tmp_path,
        exit_code=1,
        stdout=json.dumps(payload),
        stderr="",
    )

    assert result.status is CheckStatus.FAILED
    assert result.messages[0] == "structural: 1 error finding."
    selected = report_section(result.messages, "Selected view:")
    assert "3 findings (errors: 1, warnings: 1, info: 1)." in selected
    for index, severity in enumerate(("Error", "Warning", "Info"), start=1):
        message = report_section(result.messages, f"Finding {index} of 3:")
        assert message.startswith(
            f"Finding {index} of 3: {severity} [unresolved-imports]"
        )


@pytest.mark.parametrize(
    ("gate", "scope"),
    [
        (
            "structural",
            "All explicit imports, including typing-only and function-local.",
        ),
        (
            "non-typing",
            "Excludes typing-only imports; includes function-local imports.",
        ),
        (
            "module-body",
            "Excludes typing-only imports and imports in functions/methods.",
        ),
    ],
)
@pytest.mark.parametrize("package", [False, True])
def test_builtin_views_explain_import_scope_and_node_grouping(
    gate: str,
    scope: str,
    package: bool,
    tmp_path: Path,
    command_factory: CommandFactory,
) -> None:
    payload = json.loads(HEALTHY_REPORT)
    selected_gate = f"package-{gate}" if package else gate
    payload["gate"] = selected_gate
    payload["views"] = {selected_gate: payload["views"][gate]}
    result = command_factory.for_name(name=CommandName.PYARCHGRAPH).process_response(
        check_id="architecture",
        project_root=tmp_path,
        exit_code=0,
        stdout=json.dumps(payload),
        stderr="",
    )

    selected = report_section(result.messages, "Selected view:")
    assert scope in selected
    if package:
        assert (
            "Grouped by immediate package; standalone modules remain separate."
            in selected
        )
        assert (
            "Package groups combine modules; cycles between groups can exist "
            "even when individual modules have no cycles."
        ) in selected
    else:
        assert "Each node represents a source module." in selected
        assert "Package groups" not in selected


def test_custom_package_view_does_not_assume_builtin_grouping(
    tmp_path: Path, command_factory: CommandFactory
) -> None:
    payload = json.loads(custom_report_json(severity="info"))
    payload["gate"] = "package-review"
    payload["views"] = {"package-review": payload["views"]["packages"]}
    result = command_factory.for_name(name=CommandName.PYARCHGRAPH).process_response(
        check_id="architecture",
        project_root=tmp_path,
        exit_code=0,
        stdout=json.dumps(payload),
        stderr="",
    )

    selected = report_section(result.messages, "Selected view:")
    assert (
        "Custom graph view; nodes and checks are defined by the producer." in selected
    )
    assert "Grouped by immediate package" not in selected
    assert "Package groups" not in selected


@pytest.mark.parametrize(
    "limitations",
    [[], ["Dynamic imports are excluded.", "Only declared source roots are analyzed."]],
)
def test_limitations_are_visible_only_when_reported(
    limitations: list[str],
    tmp_path: Path,
    command_factory: CommandFactory,
) -> None:
    payload = json.loads(HEALTHY_REPORT)
    payload["coverage"]["limitations"] = limitations
    result = command_factory.for_name(name=CommandName.PYARCHGRAPH).process_response(
        check_id="architecture",
        project_root=tmp_path,
        exit_code=0,
        stdout=json.dumps(payload),
        stderr="",
    )

    if limitations:
        assert report_section(result.messages, "Analysis limitations") == (
            "Analysis limitations\n"
            "  - Dynamic imports are excluded.\n"
            "  - Only declared source roots are analyzed."
        )
    else:
        assert all(
            not message.startswith("Analysis limitations")
            for message in result.messages
        )


def test_requested_import_and_multiline_snippet_preserve_indentation(
    tmp_path: Path, command_factory: CommandFactory
) -> None:
    finding = {
        **import_finding(),
        "requested": "service.handlers",
        "evidence": [
            {
                **evidence(),
                "source_segment": "from service.handlers import (\n    handle,\n)",
            }
        ],
    }
    result = command_factory.for_name(name=CommandName.PYARCHGRAPH).process_response(
        check_id="architecture",
        project_root=tmp_path,
        exit_code=1,
        stdout=report_json(findings=[finding]),
        stderr="",
    )

    message = report_section(result.messages, "Finding 1 of 1:")
    assert "  Requested import: service.handlers\n" in message
    assert (
        "  Import locations:\n"
        "      src/app.py:3:1\n"
        "        from service.handlers import (\n"
        "            handle,\n"
        "        )"
    ) in message


def test_project_wide_custom_rules_need_no_locations_or_references(
    tmp_path: Path,
    command_factory: CommandFactory,
) -> None:
    payload = json.loads(custom_report_json(severity="info"))
    finding = payload["views"]["packages"]["findings"][0]["finding"]
    finding["node_ids"] = []
    finding["source_ids"] = []
    finding["evidence"] = []
    result = command_factory.for_name(name=CommandName.PYARCHGRAPH).process_response(
        check_id="architecture",
        project_root=tmp_path,
        exit_code=0,
        stdout=json.dumps(payload),
        stderr="",
    )

    assert result.status is CheckStatus.PASSED
    assert report_section(result.messages, "Finding 1 of 1:") == (
        "Finding 1 of 1: Info [group-size]\n  Consider a smaller package [large-group]"
    )


def test_import_provenance_fields_may_explicitly_be_null(
    tmp_path: Path,
    command_factory: CommandFactory,
) -> None:
    finding = {
        **import_finding(),
        "node": None,
        "evidence": [{**evidence(), "source": None, "target": None, "fact_id": None}],
    }
    result = command_factory.for_name(name=CommandName.PYARCHGRAPH).process_response(
        check_id="architecture",
        project_root=tmp_path,
        exit_code=1,
        stdout=report_json(findings=[finding]),
        stderr="",
    )

    assert result.status is CheckStatus.FAILED
    message = report_section(result.messages, "Finding 1 of 1:")
    assert "Unresolved import in app\n  Import needs review" in message
    assert "src/app.py:3:1" in message


def test_aggregated_cycles_use_node_labels_and_original_evidence(
    tmp_path: Path,
    command_factory: CommandFactory,
) -> None:
    payload = json.loads(report_json(findings=[cycle_finding("definite")]))
    view = payload["views"]["structural"]
    view["nodes"] = [
        {"id": "entry", "label": "Application group", "members": ["app"]},
        {"id": "logic", "label": "Service group", "members": ["service"]},
    ]
    cycle = view["findings"][0]["finding"]
    cycle["members"] = ["entry", "logic"]
    cycle["definite_members"] = ["entry", "logic"]
    cycle["witness"][0]["source"] = "entry"
    cycle["witness"][0]["target"] = "logic"
    cycle["witness"][1]["source"] = "logic"
    cycle["witness"][1]["target"] = "entry"
    result = command_factory.for_name(name=CommandName.PYARCHGRAPH).process_response(
        check_id="architecture",
        project_root=tmp_path,
        exit_code=1,
        stdout=json.dumps(payload),
        stderr="",
    )

    assert result.status is CheckStatus.FAILED
    message = report_section(result.messages, "Finding 1 of 1:")
    assert (
        "Confirmed cyclic nodes:\n    - Application group\n    - Service group"
        in message
    )
    assert "Application group -> Service group\n      src/app.py:3:1" in message
    assert "Service group -> Application group\n      src/service.py:3:1" in message


@pytest.mark.parametrize(
    "problem",
    [
        "missing_nodes",
        "duplicate_nodes",
        "unknown_member",
        "empty_members",
        "overlapping_members",
        "duplicate_checks",
        "disabled_check",
        "invalid_severity",
        "missing_finding",
        "unknown_cycle_node",
        "unknown_import_node",
        "unknown_evidence_source",
        "unknown_evidence_target",
    ],
)
def test_schema_08_rejects_invalid_view_protocol_references(
    problem: str,
    tmp_path: Path,
    command_factory: CommandFactory,
) -> None:
    payload = json.loads(
        report_json(findings=[cycle_finding("definite"), import_finding()])
    )
    view = payload["views"]["module-body"]
    if problem == "missing_nodes":
        del view["nodes"]
    elif problem == "duplicate_nodes":
        view["nodes"].append(view["nodes"][0])
    elif problem == "unknown_member":
        view["nodes"][0]["members"] = ["unknown-source"]
    elif problem == "empty_members":
        view["nodes"][0]["members"] = []
    elif problem == "overlapping_members":
        view["nodes"][1]["members"].append("app")
    elif problem == "duplicate_checks":
        view["enabled_check_ids"].append("cycles")
    elif problem == "disabled_check":
        view["findings"][0]["check_id"] = "disabled-check"
    elif problem == "invalid_severity":
        view["findings"][0]["severity"] = "critical"
    elif problem == "missing_finding":
        del view["findings"][0]["finding"]
    elif problem == "unknown_cycle_node":
        view["findings"][0]["finding"]["members"].append("missing-node")
    elif problem == "unknown_import_node":
        view["findings"][1]["finding"]["node"] = "missing-node"
    elif problem == "unknown_evidence_source":
        view["findings"][0]["finding"]["witness"][0]["evidence"][0]["source"] = (
            "missing-source"
        )
    else:
        view["findings"][0]["finding"]["witness"][0]["evidence"][0]["target"] = (
            "missing-source"
        )

    with pytest.raises(CheckOutputError, match="schema 0.8"):
        command_factory.for_name(name=CommandName.PYARCHGRAPH).process_response(
            check_id="architecture",
            project_root=tmp_path,
            exit_code=1,
            stdout=json.dumps(payload),
            stderr="",
        )


@pytest.mark.parametrize("field", ["node_ids", "source_ids"])
def test_custom_rules_reject_unknown_references(
    field: str,
    tmp_path: Path,
    command_factory: CommandFactory,
) -> None:
    payload = json.loads(custom_report_json(severity="error"))
    payload["views"]["packages"]["findings"][0]["finding"][field] = ["missing"]
    with pytest.raises(CheckOutputError, match="schema 0.8"):
        command_factory.for_name(name=CommandName.PYARCHGRAPH).process_response(
            check_id="architecture",
            project_root=tmp_path,
            exit_code=1,
            stdout=json.dumps(payload),
            stderr="",
        )


@pytest.mark.parametrize(
    "dependencies",
    [
        None,
        [],
        [
            {
                **dependency(),
                "source": "package:entry",
                "target": "package:logic",
            }
        ],
    ],
)
def test_view_dependencies_preserve_projected_nodes_and_source_evidence(
    dependencies: list[JsonValue] | None,
    tmp_path: Path,
    command_factory: CommandFactory,
) -> None:
    payload = json.loads(HEALTHY_REPORT)
    payload["gate"] = "package-structural"
    view = payload["views"].pop("structural")
    view["nodes"] = [
        {"id": "package:entry", "label": "Entry package", "members": ["app"]},
        {"id": "package:logic", "label": "Logic package", "members": ["service"]},
    ]
    view["dependencies"] = dependencies
    view["dependency_count"] = len(dependencies) if dependencies is not None else 0
    payload["views"]["package-structural"] = view
    stdout = json.dumps(payload)

    report = PyArchGraphReport.model_validate_json(stdout)
    document = json.loads(report.model_dump_json())
    assert document["views"]["package-structural"]["dependencies"] == dependencies
    result = command_factory.for_name(name=CommandName.PYARCHGRAPH).process_response(
        check_id="pyarchgraph-packages",
        project_root=tmp_path,
        exit_code=0,
        stdout=stdout,
        stderr="",
    )
    assert result.status is CheckStatus.PASSED
    assert result.check_id == "pyarchgraph-packages"
    assert report_section(result.messages, "Selected view:").startswith(
        "Selected view: package-structural (determines pass/fail)"
    )


@pytest.mark.parametrize("gate", ["structural", "module-body"])
def test_view_dependencies_are_required_even_for_informational_views(
    gate: str,
    tmp_path: Path,
    command_factory: CommandFactory,
) -> None:
    payload = json.loads(HEALTHY_REPORT)
    del payload["views"][gate]["dependencies"]

    with pytest.raises(CheckOutputError, match=rf"views\.{gate}\.dependencies"):
        command_factory.for_name(name=CommandName.PYARCHGRAPH).process_response(
            check_id="pyarchgraph-modules",
            project_root=tmp_path,
            exit_code=0,
            stdout=json.dumps(payload),
            stderr="",
        )


@pytest.mark.parametrize("gate", ["structural", "module-body"])
@pytest.mark.parametrize(
    "dependencies",
    [
        {},
        [None],
        [{"source": "app", "evidence": [evidence()]}],
        [{**dependency(), "unexpected": True}],
        [{**dependency(), "evidence": []}],
        [{**dependency(), "source": "missing-node"}],
        [{**dependency(), "target": "missing-node"}],
        [
            {
                **dependency(),
                "evidence": [{**evidence(), "source": "missing-source"}],
            }
        ],
        [
            {
                **dependency(),
                "evidence": [{**evidence(), "target": "missing-source"}],
            }
        ],
    ],
)
def test_malformed_view_dependencies_are_rejected_in_every_view(
    gate: str,
    dependencies: JsonValue,
    tmp_path: Path,
    command_factory: CommandFactory,
) -> None:
    payload = json.loads(HEALTHY_REPORT)
    payload["views"][gate]["dependencies"] = dependencies

    with pytest.raises(CheckOutputError, match="schema 0.8"):
        command_factory.for_name(name=CommandName.PYARCHGRAPH).process_response(
            check_id="pyarchgraph-modules",
            project_root=tmp_path,
            exit_code=0,
            stdout=json.dumps(payload),
            stderr="",
        )
