"""Run the producer's complete example corpus through the real Checksmith adapter."""

import os
import subprocess
from pathlib import Path
from typing import Literal

import pytest
from pydantic import BaseModel

from checksmith.commands.pyarchgraph import PyArchGraphCommand
from checksmith.config import Check
from checksmith.dtos import CheckStatus, CommandName, PackageType
from checksmith.runner import Runner


class ExpectedOutcome(BaseModel):
    outcome: Literal["pass", "fail", "error"]
    exit_code: Literal[0, 1, 2]


class ExampleRun(BaseModel):
    id: str
    source_root: str
    exclusions: tuple[str, ...]
    forbidden_dependencies: tuple[tuple[str, str], ...]
    expected: ExpectedOutcome
    variants: tuple[ExampleRun, ...] = ()


class ExampleCorpus(BaseModel):
    schema_version: Literal[2]
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


def test_every_project_and_variant_is_selected() -> None:
    assert len(CORPUS.projects) == 25
    assert len(RUNS) == 28


@pytest.mark.parametrize(
    ("project_id", "example"),
    RUNS,
    ids=[f"{project_id}/{run.id}" for project_id, run in RUNS],
)
def test_real_cli_examples_produce_expected_checksmith_results(
    project_id: str,
    example: ExampleRun,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = PRODUCER / "examples" / "projects" / project_id / example.source_root
    arguments = [str(source)]
    for pattern in example.exclusions:
        arguments.extend(("--exclude", pattern))
    for source_pattern, target_pattern in example.forbidden_dependencies:
        arguments.extend(("--forbid", f"{source_pattern}:{target_pattern}"))
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

    def actual_response(
        *,
        check_id: str,
        argv: tuple[str, ...],
        cwd: Path,
        heartbeat_interval_seconds: float,
    ) -> subprocess.CompletedProcess[str]:
        return completed

    monkeypatch.setattr("checksmith.commands.command.run_process", actual_response)
    check = Check(
        id=f"{project_id}/{example.id}",
        package_type=PackageType.UVX,
        package="pyarchgraph==0.5.0",
        command=CommandName.PYARCHGRAPH,
        args=tuple(arguments),
    )
    output = Runner(
        checks=(check,),
        commands={CommandName.PYARCHGRAPH: PyArchGraphCommand()},
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
        assert completed.stdout == ""
        assert completed.stderr.strip() in output.results[0].messages[0]
