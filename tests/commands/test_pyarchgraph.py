import json
from pathlib import Path
from typing import Literal

import pytest
from pydantic import JsonValue

from checksmith.commands.command import Command
from checksmith.commands.pyarchgraph import PyArchGraphCommand, PyArchGraphReport
from checksmith.commands.registry import CommandFactory
from checksmith.config import Check
from checksmith.dtos import CheckStatus, CommandName, PackageType
from checksmith.errors import CheckOutputError
from tests.conftest import FakeProcesses


def evidence() -> dict[str, JsonValue]:
    return {
        "path": "src/app.py",
        "line": 3,
        "column": 1,
        "source_segment": "import service",
        "resolution_kind": "exact_module",
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
                    }
                ],
            },
        ],
    }


def import_finding() -> dict[str, JsonValue]:
    return {
        "kind": "unresolved_import",
        "source": "app",
        "requested": None,
        "code": "missing_internal_target",
        "message": "Import needs review",
        "evidence": [
            {
                **evidence(),
                "resolution_kind": None,
                "source_segment": None,
            }
        ],
    }


def report_json(*, findings: list[JsonValue]) -> str:
    view = {
        "dependency_count": 2,
        "cyclic_source_count": 2 if findings else 0,
        "cyclic_dependency_count": 2 if findings else 0,
        "findings": findings,
    }
    return json.dumps(
        {
            "schema_version": "0.6",
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
            "views": {"structural": view, "non_typing": view, "module_body": view},
        }
    )


HEALTHY_REPORT = report_json(findings=[])
HEALTHY_MESSAGES = (
    "Analysis is complete; gate: structural; sources: 2; analyzed: 2.",
    "Selected view (structural): dependencies: 2; cyclic sources: 0; findings: 0.",
    "Informational view (non-typing): dependencies: 2; cyclic sources: 0; findings: 0.",
    "Informational view (module-body): dependencies: 2; cyclic sources: 0; findings: 0.",
)


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
        check=check, project_root=tmp_path
    )

    assert PyArchGraphCommand.run is Command.run
    assert result.status is CheckStatus.PASSED
    assert result.messages == HEALTHY_MESSAGES
    assert processes.started[0].argv == check.argv
    assert processes.started[0].cwd == tmp_path
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    ("finding", "message"),
    [
        (cycle_finding("definite"), "Definite cyclic sources: app, service"),
        (cycle_finding("possible"), "Possible dependency cycle among app, service"),
        (
            import_finding(),
            "Unresolved import in app: Import needs review",
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
    assert message in result.messages[-1]
    assert "src/app.py:3:1" in result.messages[-1]


def test_all_evidence_and_findings_are_reported(
    tmp_path: Path, command_factory: CommandFactory
) -> None:
    result = command_factory.for_name(name=CommandName.PYARCHGRAPH).process_response(
        check_id="architecture",
        project_root=tmp_path,
        exit_code=1,
        stdout=report_json(findings=[cycle_finding("definite"), import_finding()]),
        stderr="",
    )

    assert len(result.messages) == 6
    assert "app -> service" in result.messages[-2]
    assert "service -> app" in result.messages[-2]
    assert "src/service.py:3:1 (import app)" in result.messages[-2]
    assert "src/app.py:3:1 (import service)" in result.messages[-2]


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
        ("schema_version", "0.5"),
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
    with pytest.raises(CheckOutputError, match="schema 0.6"):
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
        {**import_finding(), "evidence": []},
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
    with pytest.raises(CheckOutputError, match="schema 0.6"):
        command_factory.for_name(name=CommandName.PYARCHGRAPH).process_response(
            check_id="architecture",
            project_root=tmp_path,
            exit_code=1,
            stdout=report_json(findings=[finding]),
            stderr="",
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
    result = command_factory.for_name(name=CommandName.PYARCHGRAPH).process_response(
        check_id="architecture",
        project_root=tmp_path,
        exit_code=1,
        stdout=json.dumps(payload),
        stderr="",
    )

    assert result.status is CheckStatus.FAILED
    message = result.messages[-1]
    assert message.startswith("Definite cyclic sources: app, service.")
    assert "Other component members with possible cycle involvement: plugin." in message
    assert "Witness: app -> service at src/app.py:3:1" in message
    assert "service -> app at src/service.py:3:1" in message


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
    payload["views"]["module_body"] = {
        "dependency_count": 0,
        "cyclic_source_count": 0,
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
    assert f"gate: {gate}" in result.messages[0]
    if status is CheckStatus.PASSED:
        assert result.messages == (
            "Analysis is complete; gate: module-body; sources: 2; analyzed: 2.",
            "Selected view (module-body): dependencies: 0; cyclic sources: 0; findings: 0.",
            "Informational view (structural): dependencies: 2; cyclic sources: 2; findings: 1.",
            "Informational view (non-typing): dependencies: 2; cyclic sources: 2; findings: 1.",
        )
    else:
        assert result.messages[1].startswith(f"Selected view ({gate})")
        assert all(message.startswith("Informational view") for message in result.messages[2:4])


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
    assert "analyzed: 1" in result.messages[0]
    assert result.messages[0].startswith("Analysis is incomplete")
    assert result.messages[4].startswith("src/broken.py:4:2: Error [source_syntax_error]")
    assert any("Witness:" in message for message in result.messages) == bool(findings)
    assert len(result.messages) == 5 + len(findings)
    if findings:
        assert result.messages[-1].startswith("Partial observation: Definite cyclic sources")


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
    assert "Info [declared_boundary]: A generated target was declared." in messages
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
    assert "typing-only, function-local, class body" in result.messages[-1]
    assert "conditional, exception handler, package initializer" in result.messages[-1]
    assert "Component dependencies: app -> service" in result.messages[-1]
    assert "app -> app" in result.messages[-1]


@pytest.mark.parametrize(
    ("section", "field", "value"),
    [
        ("coverage", "analyzed_source_count", True),
        ("coverage", "analyzed_source_count", "2"),
        ("coverage", "analyzed_source_count", -1),
        ("coverage", "unexpected", 1),
        ("module_body", "dependency_count", 1.5),
        ("module_body", "cyclic_source_count", -1),
        ("non_typing", "findings", {}),
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
    with pytest.raises(CheckOutputError, match="schema 0.6"):
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
    report = PyArchGraphReport.model_validate_json(json.dumps(payload))
    assert report.sources == ()
    assert report.status == "incomplete"


@pytest.mark.parametrize("source_status", ["error", "pending"])
def test_complete_report_rejects_unanalyzed_sources(
    source_status: str, tmp_path: Path, command_factory: CommandFactory
) -> None:
    payload = json.loads(HEALTHY_REPORT)
    payload["sources"][0]["analysis_status"] = source_status
    with pytest.raises(CheckOutputError, match="complete reports must contain only analyzed sources"):
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
    assert "Unacknowledged native boundary pkg.native" in result.messages[5]
    assert result.messages[6].startswith(
        "Partial observation: Definite cyclic sources: application.entry, src/service.py."
    )
    assert "application.entry -> src/service.py at" in result.messages[6]
    assert "src/service.py -> application.entry at" in result.messages[6]
    assert result.messages[7].startswith(
        "Partial observation: Unresolved import in application.entry:"
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
        payload["views"]["module_body"]["findings"] = [
            {**import_finding(), "source": "uninventoried-source"}
        ]
    else:
        payload["sources"] = []
        payload["coverage"]["analyzed_source_count"] = 0
    with pytest.raises(CheckOutputError, match="schema 0.6"):
        command_factory.for_name(name=CommandName.PYARCHGRAPH).process_response(
            check_id="architecture",
            project_root=tmp_path,
            exit_code=0,
            stdout=json.dumps(payload),
            stderr="",
        )
