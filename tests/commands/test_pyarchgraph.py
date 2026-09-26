import json
from pathlib import Path
from typing import Literal

import pytest
from pydantic import JsonValue

from checksmith.commands.command import Command
from checksmith.commands.pyarchgraph import PyArchGraphCommand
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
    }


def dependency() -> dict[str, JsonValue]:
    return {"source": "app", "target": "service", "evidence": [evidence()]}


def cycle_finding(certainty: Literal["definite", "possible"]) -> dict[str, JsonValue]:
    return {
        "kind": "cycle",
        "certainty": certainty,
        "members": ["app", "service"],
        "definite_members": ["app", "service"] if certainty == "definite" else [],
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


def forbidden_finding() -> dict[str, JsonValue]:
    return {
        "kind": "forbidden_dependency",
        "certainty": "definite",
        "rules": [["app", "service"], ["app*", "*"]],
        "witness": [dependency()],
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
    return json.dumps(
        {
            "schema_version": "0.5",
            "module_count": 2,
            "dependency_count": 2,
            "findings": findings,
        }
    )


HEALTHY_REPORT = report_json(findings=[])


def test_command_uses_normal_subprocess_execution(
    tmp_path: Path,
    processes: FakeProcesses,
) -> None:
    check = Check(
        id="architecture",
        package_type=PackageType.UVX,
        package="pyarchgraph==0.5.0",
        command=CommandName.PYARCHGRAPH,
        args=("src", "--exclude", "vendor/*", "--forbid", "app:service"),
    )
    processes.stdout = HEALTHY_REPORT

    result = PyArchGraphCommand().run(check=check, project_root=tmp_path)

    assert PyArchGraphCommand.run is Command.run
    assert result.status is CheckStatus.PASSED
    assert result.messages == ("Modules: 2; dependencies: 2.",)
    assert processes.started[0].argv == check.argv
    assert processes.started[0].cwd == tmp_path
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    ("finding", "message"),
    [
        (cycle_finding("definite"), "Definite cyclic modules: app, service"),
        (cycle_finding("possible"), "Possible dependency cycle among app, service"),
        (
            forbidden_finding(),
            "Definite forbidden dependency (rules: app:service, app*:*)",
        ),
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
) -> None:
    result = PyArchGraphCommand().process_response(
        check_id="architecture",
        project_root=tmp_path,
        exit_code=1,
        stdout=report_json(findings=[finding]),
        stderr="",
    )

    assert result.status is CheckStatus.FAILED
    assert result.check_id == "architecture"
    assert len(result.messages) == 2
    assert message in result.messages[1]
    assert "src/app.py:3:1" in result.messages[1]


def test_all_evidence_and_findings_are_reported(tmp_path: Path) -> None:
    result = PyArchGraphCommand().process_response(
        check_id="architecture",
        project_root=tmp_path,
        exit_code=1,
        stdout=report_json(findings=[cycle_finding("definite"), forbidden_finding()]),
        stderr="",
    )

    assert len(result.messages) == 3
    assert "app -> service" in result.messages[1]
    assert "service -> app" in result.messages[1]
    assert "src/service.py:3:1 (import app)" in result.messages[1]
    assert "src/app.py:3:1 (import service)" in result.messages[1]


@pytest.mark.parametrize("exit_code", [-9, 2, 3, 127])
def test_analysis_and_unexpected_exits_are_errors(
    exit_code: int, tmp_path: Path
) -> None:
    with pytest.raises(CheckOutputError, match=f"exited {exit_code}") as caught:
        PyArchGraphCommand().process_response(
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
) -> None:
    with pytest.raises(CheckOutputError, match="exit code disagrees"):
        PyArchGraphCommand().process_response(
            check_id="architecture",
            project_root=tmp_path,
            exit_code=exit_code,
            stdout=report_json(findings=findings),
            stderr="",
        )


@pytest.mark.parametrize(
    "stdout", ["", "not json", "[]", "{}", HEALTHY_REPORT + "noise"]
)
def test_stdout_must_be_a_complete_json_report(stdout: str, tmp_path: Path) -> None:
    with pytest.raises(CheckOutputError, match="Invalid PyArchGraph report on stdout"):
        PyArchGraphCommand().process_response(
            check_id="architecture",
            project_root=tmp_path,
            exit_code=0,
            stdout=stdout,
            stderr="",
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", "0.4"),
        ("module_count", 0),
        ("module_count", True),
        ("module_count", "2"),
        ("dependency_count", -1),
        ("dependency_count", 1.5),
        ("findings", {}),
        ("unexpected", "extra"),
    ],
)
def test_report_schema_is_strict(field: str, value: JsonValue, tmp_path: Path) -> None:
    payload = json.loads(HEALTHY_REPORT)
    payload[field] = value
    with pytest.raises(CheckOutputError, match="schema 0.5"):
        PyArchGraphCommand().process_response(
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
        {**forbidden_finding(), "rules": []},
        {**forbidden_finding(), "witness": [dependency(), dependency()]},
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
def test_malformed_findings_are_errors(finding: JsonValue, tmp_path: Path) -> None:
    with pytest.raises(CheckOutputError, match="schema 0.5"):
        PyArchGraphCommand().process_response(
            check_id="architecture",
            project_root=tmp_path,
            exit_code=1,
            stdout=report_json(findings=[finding]),
            stderr="",
        )


def test_mixed_component_does_not_overstate_possible_members(tmp_path: Path) -> None:
    finding = {
        **cycle_finding("definite"),
        "members": ["app", "plugin", "service"],
    }
    result = PyArchGraphCommand().process_response(
        check_id="architecture",
        project_root=tmp_path,
        exit_code=1,
        stdout=report_json(findings=[finding]),
        stderr="",
    )

    assert result.status is CheckStatus.FAILED
    message = result.messages[1]
    assert message.startswith("Definite cyclic modules: app, service.")
    assert "Other component members with possible cycle involvement: plugin." in message
    assert "Witness: app -> service at src/app.py:3:1" in message
    assert "service -> app at src/service.py:3:1" in message
