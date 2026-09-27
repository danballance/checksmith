"""Run the producer's complete example corpus through the real Checksmith adapter."""

import os
import subprocess
from pathlib import Path
from typing import Literal

import pytest
from pydantic import BaseModel

from checksmith.commands.pyarchgraph import PyArchGraphReport
from checksmith.config import Check
from checksmith.dtos import CheckResult, CheckStatus, CommandName, PackageType
from checksmith.runner import Runner
from tests.conftest import FakeProcesses, make_command_factory


class ExpectedOutcome(BaseModel):
    outcome: Literal["pass", "fail", "error"]
    exit_code: Literal[0, 1, 2]
    status: Literal["complete", "incomplete"]


class ExampleRun(BaseModel):
    id: str
    source_roots: tuple[str, ...]
    exclusions: tuple[str, ...]
    gate: str
    details: Literal["summary", "component-edges"]
    config: str | None
    expected: ExpectedOutcome
    variants: tuple[ExampleRun, ...] = ()


class ExampleCorpus(BaseModel):
    schema_version: Literal[3]
    projects: tuple[ExampleRun, ...]


PRODUCER = Path(
    os.environ.get(
        "PYARCHGRAPH_SOURCE",
        Path(__file__).resolve().parents[3] / "pyarchgraph",
    )
).resolve()
if not PRODUCER.exists() and "PYARCHGRAPH_SOURCE" not in os.environ:
    pytest.skip(
        "Set PYARCHGRAPH_SOURCE to run the producer's example acceptance suite",
        allow_module_level=True,
    )
CORPUS = ExampleCorpus.model_validate_json(
    (PRODUCER / "examples" / "manifest.json").read_bytes()
)
RUNS = [
    (project.id, run)
    for project in CORPUS.projects
    for run in (project, *project.variants)
]

CUSTOM_PRODUCER = """\
import sys
from pathlib import Path

from examples.custom_strategies import PackageGroupingView
from pyarchgraph import (
    AnalysisOptions,
    ApplicationFactory,
    CheckContext,
    CheckRegistration,
    CheckResult,
    JsonReportRenderer,
    RuleFinding,
    Severity,
    ViewRegistration,
)


class ProjectReviewCheck:
    def __init__(self, severity: Severity) -> None:
        self.severity = severity

    def evaluate(self, context: CheckContext) -> tuple[CheckResult, ...]:
        return tuple(
            CheckResult(
                severity=self.severity,
                finding=RuleFinding(
                    code="package-review",
                    message=f"Review package {node.label}.",
                    node_ids=(node.id,),
                    source_ids=node.members,
                    evidence=(),
                ),
            )
            for node in context.graph.nodes
        )


factory = ApplicationFactory(
    views=ApplicationFactory.default_views()
    + (ViewRegistration("packages", PackageGroupingView()),),
    checks=ApplicationFactory.default_checks()
    + (
        CheckRegistration(
            "project-review", ProjectReviewCheck(Severity(sys.argv[2])), ("packages",)
        ),
    ),
    check_selection={
        "packages": tuple(sys.argv[3].split(",")),
        "structural": (),
        "non-typing": (),
    },
)
report = factory.create_analyzer().analyse(
    (Path("."),),
    options=AnalysisOptions(gate=sys.argv[4], details="component-edges"),
    base_dir=Path(sys.argv[1]),
)
print(JsonReportRenderer().render(report))
raise SystemExit(report.exit_code)
"""


@pytest.fixture
def projected_packages(tmp_path: Path) -> Path:
    for package in ("one", "two"):
        directory = tmp_path / package
        directory.mkdir()
        (directory / "__init__.py").write_text("", encoding="utf-8")
        (directory / "leaf.py").write_text("", encoding="utf-8")
    (tmp_path / "one" / "a.py").write_text(
        "import two.leaf\nimport one.leaf\n", encoding="utf-8"
    )
    (tmp_path / "two" / "b.py").write_text("import one.leaf\n", encoding="utf-8")
    return tmp_path


def run_custom_producer(
    *,
    project: Path,
    severity: Literal["info", "warning", "error"],
    checks: tuple[str, ...],
    gate: str,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        (
            "uv",
            "run",
            "--project",
            str(PRODUCER),
            "--no-sync",
            "python",
            "-c",
            CUSTOM_PRODUCER,
            str(project),
            severity,
            ",".join(checks),
            gate,
        ),
        cwd=PRODUCER,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=30,
    )


def read_custom_producer(
    *, completed: subprocess.CompletedProcess[str], project: Path
) -> tuple[PyArchGraphReport, CheckResult]:
    report = PyArchGraphReport.model_validate_json(completed.stdout)
    result = (
        make_command_factory(executor=FakeProcesses())
        .for_name(name=CommandName.PYARCHGRAPH)
        .process_response(
            check_id="custom-architecture",
            project_root=project,
            exit_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )
    )
    return report, result


def test_every_project_and_variant_is_selected() -> None:
    assert CORPUS.projects
    assert len(RUNS) == sum(1 + len(project.variants) for project in CORPUS.projects)
    assert len({(project_id, run.id) for project_id, run in RUNS}) == len(RUNS)
    assert {project_id for project_id, _ in RUNS} == {
        project.id for project in CORPUS.projects
    }


@pytest.mark.parametrize(
    ("project_id", "example"),
    RUNS,
    ids=[f"{project_id}/{run.id}" for project_id, run in RUNS],
)
def test_real_cli_examples_produce_expected_checksmith_results(
    project_id: str,
    example: ExampleRun,
) -> None:
    project = PRODUCER / "examples" / "projects" / project_id
    arguments = [str(project / root) for root in example.source_roots]
    arguments.extend(("--gate", example.gate, "--details", example.details))
    for pattern in example.exclusions:
        arguments.extend(("--exclude", pattern))
    if example.config is not None:
        arguments.extend(("--config", str(project / example.config)))
    completed = subprocess.run(
        (
            "uv",
            "run",
            "--project",
            str(PRODUCER),
            "--no-sync",
            "python",
            "-m",
            "pyarchgraph",
            *arguments,
        ),
        cwd=PRODUCER,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=30,
    )
    assert completed.returncode == example.expected.exit_code, completed.stderr
    report = PyArchGraphReport.model_validate_json(completed.stdout)
    assert report.schema_version == "0.7"
    assert report.status == example.expected.status
    assert report.gate == example.gate

    processes = FakeProcesses()
    processes.exit_code = completed.returncode
    processes.stdout = completed.stdout
    processes.stderr = completed.stderr
    command_factory = make_command_factory(executor=processes)
    check = Check(
        id=f"{project_id}/{example.id}",
        package_type=PackageType.UVX,
        package="pyarchgraph @ git+https://github.com/danballance/pyarchgraph@main",
        command=CommandName.PYARCHGRAPH,
        args=tuple(arguments),
    )
    output = Runner(
        checks=(check,),
        commands=command_factory.registry(),
        project_root=PRODUCER,
    ).check()

    expected = {
        "pass": CheckStatus.PASSED,
        "fail": CheckStatus.FAILED,
        "error": CheckStatus.ERROR,
    }[example.expected.outcome]
    assert output.results[0].status is expected, output.results[0].messages
    if expected is CheckStatus.FAILED:
        assert len(output.results[0].messages) > 1
        assert any(".py:" in message for message in output.results[0].messages)
    if expected is CheckStatus.ERROR:
        assert completed.stdout
        assert any(
            "Analysis is incomplete" in msg for msg in output.results[0].messages
        )


@pytest.mark.parametrize(
    ("severity", "exit_code", "status"),
    [
        ("info", 0, CheckStatus.PASSED),
        ("warning", 0, CheckStatus.PASSED),
        ("error", 1, CheckStatus.FAILED),
    ],
)
def test_real_custom_rule_envelopes_preserve_severity_and_projected_references(
    projected_packages: Path,
    severity: Literal["info", "warning", "error"],
    exit_code: int,
    status: CheckStatus,
) -> None:
    completed = run_custom_producer(
        project=projected_packages,
        severity=severity,
        checks=("project-review",),
        gate="packages",
    )

    assert completed.returncode == exit_code, completed.stderr
    report, result = read_custom_producer(
        completed=completed, project=projected_packages
    )
    assert report.schema_version == "0.7"
    assert report.status == "complete"
    assert report.gate == "packages"
    selected = report.views["packages"]
    assert selected.cyclic_node_count == 2
    assert selected.dependency_count == 2
    assert report.views["structural"].cyclic_node_count == 0
    assert selected.enabled_check_ids == ("project-review",)
    source_ids = {source.id for source in report.sources}
    nodes = {node.id: node for node in selected.nodes}
    node_ids = set(nodes)
    assert node_ids == {"package:one", "package:two"}
    assert node_ids.isdisjoint(source_ids)
    assert {member for node in selected.nodes for member in node.members} == source_ids
    assert len(selected.findings) == 2
    for envelope in selected.findings:
        assert envelope.check_id == "project-review"
        assert envelope.severity == severity
        finding = envelope.finding
        assert finding.kind == "rule"
        assert finding.code == "package-review"
        assert len(finding.node_ids) == 1
        assert finding.node_ids[0] in nodes
        assert finding.source_ids == nodes[finding.node_ids[0]].members
    assert result.status is status
    rendered = tuple(
        message
        for message in result.messages
        if message.startswith(f"{severity.capitalize()} [project-review]: ")
    )
    assert len(rendered) == 2
    assert all("package-review" in message for message in rendered)
    assert any("Review package one." in message for message in rendered)
    assert any("Review package two." in message for message in rendered)


def test_real_builtin_cycle_check_uses_custom_view_node_labels(
    projected_packages: Path,
) -> None:
    completed = run_custom_producer(
        project=projected_packages,
        severity="info",
        checks=("cycles",),
        gate="packages",
    )

    assert completed.returncode == 1, completed.stderr
    report, result = read_custom_producer(
        completed=completed, project=projected_packages
    )
    selected = report.views["packages"]
    assert selected.enabled_check_ids == ("cycles",)
    assert len(selected.findings) == 1
    envelope = selected.findings[0]
    assert envelope.check_id == "cycles"
    assert envelope.severity == "error"
    finding = envelope.finding
    assert finding.kind == "cycle"
    assert set(finding.members) == {"package:one", "package:two"}
    assert result.status is CheckStatus.FAILED
    messages = tuple(
        message for message in result.messages if message.startswith("Error [cycles]: ")
    )
    assert len(messages) == 1
    assert "one -> two" in messages[0]
    assert "two -> one" in messages[0]
    assert "one/a.py:1:1" in messages[0]
    assert "two/b.py:1:1" in messages[0]


def test_real_unselected_custom_errors_do_not_fail_a_clean_selected_gate(
    projected_packages: Path,
) -> None:
    completed = run_custom_producer(
        project=projected_packages,
        severity="error",
        checks=("project-review",),
        gate="structural",
    )

    assert completed.returncode == 0, completed.stderr
    report, result = read_custom_producer(
        completed=completed, project=projected_packages
    )
    assert report.gate == "structural"
    assert report.views["structural"].findings == ()
    assert len(report.views["packages"].findings) == 2
    assert all(
        envelope.severity == "error" for envelope in report.views["packages"].findings
    )
    assert result.status is CheckStatus.PASSED
    assert any("packages" in message for message in result.messages)
