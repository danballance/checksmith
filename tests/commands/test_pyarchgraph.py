import json
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Final

import pytest
from pydantic import JsonValue

from checksmith.commands.command import Command
from checksmith.commands.pyarchgraph import PyArchGraphCommand
from checksmith.config import Argument, Check, ConfigPath
from checksmith.dtos import CheckResult, CheckStatus, CommandName, PackageType
from checksmith.errors import CheckOutputError
from tests.conftest import FakeProcesses

ARGS: Final = (
    "src",
    "--project-root",
    ".",
    "--output",
    "json",
    "--output-dir",
    "build",
)


def architecture_check(*, args: tuple[Argument, ...]) -> Check:
    return Check(
        id="architecture",
        package_type=PackageType.UVX,
        package="pyarchgraph==0.4.0",
        command=CommandName.PYARCHGRAPH,
        args=args,
    )


def report_json(*, known: JsonValue, possible: JsonValue) -> str:
    return json.dumps(
        {
            "schema_version": "0.4",
            "analysis": {
                "provenance": {
                    "analyser": {"version": "0.4.0"},
                    "source_root": "src",
                    "graph_policy": {"include_tests": False},
                },
                "modules": ["app"],
            },
            "cleanup": {
                "violation_count": known,
                "possible_violation_count": possible,
                "violations": [{"evidence": {"path": "src/app.py", "line": 3}}],
                "work_items": [{"id": "cycle:app"}],
                "coverage": {"complete": False},
            },
        }
    )


def write_report(*, path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def stub_analysis(
    *, monkeypatch: pytest.MonkeyPatch, report_path: Path, content: str | None
) -> list[Check]:
    calls: list[Check] = []

    def run(self: Command, *, check: Check, project_root: Path) -> CheckResult:
        calls.append(check)
        assert not report_path.exists(), "Previous report must be removed before run"
        if content is not None:
            write_report(path=report_path, content=content)
        return CheckResult(check_id=check.id, status=CheckStatus.PASSED, messages=())

    monkeypatch.setattr(Command, "run", run)
    return calls


def test_prepare_replaces_a_baseline_and_preserves_the_complete_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    baseline = tmp_path / "build/baseline/dependency-graph.json"
    current = tmp_path / "build/current/dependency-graph.json"
    write_report(path=baseline, content=report_json(known=9, possible=8))
    write_report(path=current, content="An earlier current report")
    content = report_json(known=3, possible=2)
    calls = stub_analysis(monkeypatch=monkeypatch, report_path=baseline, content=content)
    check = architecture_check(args=ARGS)

    result = PyArchGraphCommand().prepare(check=check, project_root=tmp_path)

    assert result.status is CheckStatus.PASSED
    assert result.check_id == "architecture"
    assert "cleanup.violation_count=3" in result.messages[0]
    assert "cleanup.possible_violation_count=2" in result.messages[0]
    assert str(baseline) in result.messages[0]
    assert baseline.read_text(encoding="utf-8") == content
    assert current.read_text(encoding="utf-8") == "An earlier current report"
    assert calls[0].args == (*ARGS[:-1], str(baseline.parent))
    assert check.args == ARGS


@pytest.mark.parametrize(
    ("known", "possible", "status"),
    [
        (3, 2, CheckStatus.PASSED),
        (2, 1, CheckStatus.PASSED),
        (4, 2, CheckStatus.FAILED),
        (3, 3, CheckStatus.FAILED),
        (2, 3, CheckStatus.FAILED),
        (4, 1, CheckStatus.FAILED),
    ],
)
def test_each_counter_is_compared_independently_with_the_original_baseline(
    known: int,
    possible: int,
    status: CheckStatus,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline = tmp_path / "build/baseline/dependency-graph.json"
    current = tmp_path / "build/current/dependency-graph.json"
    original = report_json(known=3, possible=2)
    write_report(path=baseline, content=original)
    write_report(path=current, content=report_json(known=0, possible=0))
    content = report_json(known=known, possible=possible)
    stub_analysis(monkeypatch=monkeypatch, report_path=current, content=content)

    result = PyArchGraphCommand().run(
        check=architecture_check(args=ARGS),
        project_root=tmp_path,
    )

    assert result.status is status
    assert baseline.read_text(encoding="utf-8") == original
    assert current.read_text(encoding="utf-8") == content
    assert f"cleanup.violation_count: baseline=3, current={known}" in result.messages[0]
    assert f"baseline=2, current={possible}" in result.messages[0]
    assert str(baseline) in result.messages[1]
    assert str(current) in result.messages[1]
    if status is CheckStatus.FAILED:
        for instruction in (
            "cleanup.violations",
            "cleanup.work_items",
            "cleanup.coverage",
            "rerun checksmith check",
            "both counts are at or below",
            "do not rerun checksmith prepare",
        ):
            assert instruction in result.messages[2]


def test_module_additions_do_not_prevent_comparison(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = report_json(known=0, possible=0)
    write_report(
        path=tmp_path / "build/baseline/dependency-graph.json", content=original
    )
    stub_analysis(
        monkeypatch=monkeypatch,
        report_path=tmp_path / "build/current/dependency-graph.json",
        content=original.replace('["app"]', '["app", "app.new"]'),
    )

    result = PyArchGraphCommand().run(
        check=architecture_check(args=ARGS),
        project_root=tmp_path,
    )

    assert result.status is CheckStatus.PASSED


def test_missing_baseline_fails_before_running_the_tool(
    tmp_path: Path, processes: FakeProcesses
) -> None:
    with pytest.raises(CheckOutputError, match="checksmith prepare") as raised:
        PyArchGraphCommand().run(
            check=architecture_check(args=ARGS),
            project_root=tmp_path,
        )

    assert str(tmp_path / "build/baseline/dependency-graph.json") in str(raised.value)
    assert not processes.started


@pytest.mark.parametrize("stage", ["baseline", "current"])
@pytest.mark.parametrize(
    "content",
    [
        "not JSON",
        "[]",
        "{}",
        (
            '{"schema_version": "0.4", "analysis": {"provenance": {}}, '
            '"cleanup": {"violation_count": 0, "possible_violation_count": 0}}'
        ),
        report_json(known=-1, possible=0),
        report_json(known=0, possible=-1),
        report_json(known=True, possible=0),
        report_json(known=0, possible=True),
        report_json(known="1", possible=0),
        report_json(known=0, possible=1.5),
        report_json(known=0, possible=0).replace('"0.4"', '"0.3"'),
        report_json(known=0, possible=0).replace('"provenance": {', '"missing": {'),
        report_json(known=0, possible=0).replace('"cleanup": {', '"missing": {'),
    ],
)
def test_malformed_reports_are_errors(
    stage: str,
    content: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    valid = report_json(known=0, possible=0)
    write_report(
        path=tmp_path / "build/baseline/dependency-graph.json",
        content=content if stage == "baseline" else valid,
    )
    calls = stub_analysis(
        monkeypatch=monkeypatch,
        report_path=tmp_path / "build/current/dependency-graph.json",
        content=content if stage == "current" else valid,
    )

    with pytest.raises(CheckOutputError, match="Cannot read PyArchGraph report"):
        PyArchGraphCommand().run(
            check=architecture_check(args=ARGS),
            project_root=tmp_path,
        )

    assert len(calls) == (0 if stage == "baseline" else 1)


@pytest.mark.parametrize("setting", ["true", "0"])
def test_changed_analysis_settings_are_an_error(
    setting: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = report_json(known=0, possible=0)
    write_report(
        path=tmp_path / "build/baseline/dependency-graph.json", content=original
    )
    stub_analysis(
        monkeypatch=monkeypatch,
        report_path=tmp_path / "build/current/dependency-graph.json",
        content=original.replace(
            '"include_tests": false', f'"include_tests": {setting}'
        ),
    )

    with pytest.raises(CheckOutputError, match="incompatible analysis settings"):
        PyArchGraphCommand().run(
            check=architecture_check(args=ARGS),
            project_root=tmp_path,
        )


@pytest.mark.parametrize("prepare", [True, False])
def test_a_stale_report_cannot_pass_when_the_tool_writes_nothing(
    prepare: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    baseline = tmp_path / "build/baseline/dependency-graph.json"
    current = tmp_path / "build/current/dependency-graph.json"
    write_report(path=baseline, content=report_json(known=0, possible=0))
    write_report(path=current, content=report_json(known=0, possible=0))
    target = baseline if prepare else current
    stub_analysis(monkeypatch=monkeypatch, report_path=target, content=None)
    command = PyArchGraphCommand()
    operation = command.prepare if prepare else command.run

    with pytest.raises(CheckOutputError, match="Cannot read PyArchGraph report"):
        operation(
            check=architecture_check(args=ARGS),
            project_root=tmp_path,
        )

    assert not target.exists()


@pytest.mark.parametrize("exit_code", [1, 2, 3, 4, -9])
def test_nonzero_tool_exits_are_errors(exit_code: int) -> None:
    with pytest.raises(CheckOutputError) as raised:
        PyArchGraphCommand().process_response(
            check_id="architecture",
            project_root=Path("/workspace"),
            exit_code=exit_code,
            stdout="Details from stdout\n",
            stderr="Analysis failed\n",
        )

    assert raised.value.command is CommandName.PYARCHGRAPH
    assert raised.value.check_id == "architecture"
    assert f"pyarchgraph exited {exit_code}" in str(raised.value)
    assert "Analysis failed" in str(raised.value)
    assert "Details from stdout" in str(raised.value)


@pytest.mark.parametrize(
    "args",
    [
        ("src",),
        ("src", "--output", "json"),
        (*ARGS, "--output", "json"),
        (*ARGS, "--output-dir", "another"),
        (*ARGS, "--output", "graph"),
        (*ARGS, "--check"),
        (*ARGS, "--check=true"),
        (*ARGS, "--baseline", "old.json"),
        (*ARGS, "--cleanup-baseline=old.json"),
        (*ARGS, "--allow-inventory-change"),
        (*ARGS, "--json-only"),
        (*ARGS, "--", "--check"),
        (*ARGS, "--out=json"),
        (*ARGS, "--output="),
        (*ARGS, "--output-dir"),
        (*ARGS, "--output-dir="),
        (*ARGS, "--include-tests=true"),
        ("src", "--exclude", "--output", "json", "--output-dir", "current"),
    ],
)
def test_invalid_arguments_fail_before_running(
    args: tuple[str, ...], tmp_path: Path, processes: FakeProcesses
) -> None:
    with pytest.raises(CheckOutputError, match="Invalid PyArchGraph arguments"):
        PyArchGraphCommand().prepare(
            check=architecture_check(args=args),
            project_root=tmp_path,
        )

    assert not processes.started


@pytest.mark.parametrize("alias", ["directory_symlink", "file_symlink"])
def test_baseline_and_current_report_paths_must_differ(
    alias: str, tmp_path: Path, processes: FakeProcesses
) -> None:
    baseline_dir = tmp_path / "build/baseline"
    baseline_dir.mkdir(parents=True)
    baseline = baseline_dir / "dependency-graph.json"
    baseline.write_text(report_json(known=0, possible=0), encoding="utf-8")
    current_dir = tmp_path / "build/current"
    if alias == "directory_symlink":
        current_dir.symlink_to(baseline_dir, target_is_directory=True)
    else:
        current_dir.mkdir()
        (current_dir / "dependency-graph.json").symlink_to(baseline)

    with pytest.raises(CheckOutputError, match="must name different reports"):
        PyArchGraphCommand().prepare(
            check=architecture_check(args=ARGS),
            project_root=tmp_path,
        )

    assert baseline.exists()
    assert not processes.started


@pytest.mark.parametrize(
    ("root_kind", "inline"),
    [
        ("relative", False),
        ("relative", True),
        ("absolute", False),
        ("absolute", True),
        ("config_path", False),
    ],
)
@pytest.mark.parametrize("relative_project_root", [True, False])
def test_both_operations_use_subdirectories_and_preserve_other_arguments(
    root_kind: str,
    inline: bool,
    relative_project_root: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    project_directory = tmp_path / "project"
    project_directory.mkdir()
    project_root = Path("project") if relative_project_root else project_directory
    output_root = project_directory / "build reports"
    directory: Argument
    if root_kind == "relative":
        directory = "build reports"
    elif root_kind == "absolute":
        directory = str(output_root)
    else:
        directory = ConfigPath.model_validate(
            {"config_path": "../build reports"},
            context=project_directory / ".config/settings.yaml",
        )
    output_args = (
        (f"--output-dir={directory}",) if inline else ("--output-dir", directory)
    )
    prefix = ("src", "--project-root", ".", "--output=json")
    suffix = (
        "--exclude=--check",
        "--exclude",
        "--cleanup-baseline",
        "--include-tests",
    )
    args = (*prefix, *output_args, *suffix)
    check = architecture_check(args=args)
    command = PyArchGraphCommand()
    for stage, operation in (("baseline", command.prepare), ("current", command.run)):
        report = output_root / stage / "dependency-graph.json"
        calls = stub_analysis(
            monkeypatch=monkeypatch,
            report_path=report,
            content=report_json(known=0, possible=0),
        )

        result = operation(check=check, project_root=project_root)

        expected_output_args = (
            (f"--output-dir={report.parent}",)
            if inline
            else ("--output-dir", str(report.parent))
        )
        assert result.status is CheckStatus.PASSED
        assert calls[0].args == (*prefix, *expected_output_args, *suffix)
        assert check.args == args


def test_one_shared_adapter_keeps_two_configurations_separate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    command = PyArchGraphCommand()
    for index in (1, 2):
        output_root = Path(f"build/reports-{index}")
        check = architecture_check(
            args=(*ARGS[:-1], str(output_root))
        ).model_copy(update={"id": f"architecture-{index}"})
        baseline = tmp_path / output_root / "baseline/dependency-graph.json"
        current = tmp_path / output_root / "current/dependency-graph.json"
        content = report_json(known=index, possible=index)
        stub_analysis(monkeypatch=monkeypatch, report_path=baseline, content=content)
        prepared = command.prepare(check=check, project_root=tmp_path)
        stub_analysis(monkeypatch=monkeypatch, report_path=current, content=content)

        result = command.run(check=check, project_root=tmp_path)

        assert prepared.check_id == result.check_id == f"architecture-{index}"
        assert result.status is CheckStatus.PASSED
        assert baseline.read_text(encoding="utf-8") == content


def test_failure_to_remove_a_previous_report_is_an_error(
    tmp_path: Path, processes: FakeProcesses
) -> None:
    report = tmp_path / "build/baseline/dependency-graph.json"
    report.mkdir(parents=True)

    with pytest.raises(CheckOutputError, match="Cannot remove previous"):
        PyArchGraphCommand().prepare(
            check=architecture_check(args=ARGS),
            project_root=tmp_path,
        )

    assert not processes.started


def test_prepare_removes_a_malformed_baseline_after_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    baseline = tmp_path / "build/baseline/dependency-graph.json"
    stub_analysis(monkeypatch=monkeypatch, report_path=baseline, content="{}")

    with pytest.raises(CheckOutputError, match="Cannot read PyArchGraph report"):
        PyArchGraphCommand().prepare(
            check=architecture_check(args=ARGS),
            project_root=tmp_path,
        )

    assert not baseline.exists()


@pytest.mark.parametrize("exit_code", [0, 1])
def test_preparation_requires_a_successful_process_and_a_fresh_valid_report(
    exit_code: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    processes: FakeProcesses,
) -> None:
    baseline = tmp_path / "build/baseline/dependency-graph.json"

    def run(
        argv: Sequence[str],
        *,
        cwd: str,
        stdin: int,
        capture_output: bool,
        text: bool,
        encoding: str,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        output_dir = Path(argv[argv.index("--output-dir") + 1])
        write_report(
            path=output_dir / "dependency-graph.json",
            content=report_json(known=1, possible=2),
        )
        return processes.run(
            argv,
            cwd=cwd,
            stdin=stdin,
            capture_output=capture_output,
            text=text,
            encoding=encoding,
            check=check,
        )

    monkeypatch.setattr(subprocess, "run", run)
    processes.exit_code = exit_code
    command = PyArchGraphCommand()
    check = architecture_check(args=ARGS)
    if exit_code == 0:
        result = command.prepare(check=check, project_root=tmp_path)
        assert result.status is CheckStatus.PASSED
        result = command.run(check=check, project_root=tmp_path)
        assert result.status is CheckStatus.PASSED
        assert processes.started[1].argv == (
            "uvx",
            "--from",
            "pyarchgraph==0.4.0",
            "pyarchgraph",
            *ARGS[:-1],
            str(tmp_path / "build/current"),
        )
        assert processes.started[1].cwd == tmp_path
    else:
        with pytest.raises(CheckOutputError, match="pyarchgraph exited 1"):
            command.prepare(check=check, project_root=tmp_path)
        assert not baseline.exists()
        with pytest.raises(CheckOutputError, match="Cannot read PyArchGraph report"):
            command.run(check=check, project_root=tmp_path)
        assert len(processes.started) == 1
    assert processes.started[0].argv == (
        "uvx",
        "--from",
        "pyarchgraph==0.4.0",
        "pyarchgraph",
        *ARGS[:-1],
        str(baseline.parent),
    )
    assert processes.started[0].cwd == tmp_path
