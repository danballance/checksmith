import json
from collections.abc import Callable
from pathlib import Path
from typing import Final

import pytest

from checksmith.commands.ty import TyCommand
from checksmith.config import Check
from checksmith.dtos import CheckResult, CheckStatus, CommandName, PackageType
from checksmith.errors import CheckOutputError
from tests.conftest import FakeProcesses

PROJECT_ROOT = Path("/workspace/project")

TY_REPORT: Final = r"""[
  {
    "check_name": "invalid-assignment",
    "description": "invalid-assignment: Object of type `Literal[\"text\"]` is not assignable to `int`",
    "severity": "major",
    "fingerprint": "e20459c8a7e22285",
    "location": {
      "path": "/workspace/project/example.py",
      "positions": {
        "begin": {"line": 1, "column": 14},
        "end": {"line": 1, "column": 20}
      }
    }
  }
]"""


def read_ty(*, stdout: str, exit_code: int, stderr: str) -> CheckResult:
    return TyCommand().process_response(
        check_id="types",
        project_root=PROJECT_ROOT,
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
    )


def test_importing_ty_does_not_load_other_command_implementations(
    imported_modules: Callable[[str], frozenset[str]],
) -> None:
    modules = imported_modules("checksmith.commands.ty")

    assert not modules & {
        "checksmith.commands.registry",
        "checksmith.commands.import_linter",
        "checksmith.commands.ruff",
        "checksmith.commands.semgrep",
    }


def test_ty_without_findings_is_a_passing_check() -> None:
    assert read_ty(stdout="[]", exit_code=0, stderr="") == CheckResult(
        check_id="types", status=CheckStatus.PASSED, messages=()
    )


@pytest.mark.parametrize("exit_code", [0, 1])
@pytest.mark.parametrize("severity", ["major", "minor"])
def test_ty_errors_and_warnings_fail_even_with_exit_zero(
    exit_code: int,
    severity: str,
) -> None:
    result = read_ty(
        stdout=TY_REPORT.replace('"major"', json.dumps(severity)),
        exit_code=exit_code,
        stderr="",
    )

    assert result == CheckResult(
        check_id="types",
        status=CheckStatus.FAILED,
        messages=(
            (
                "example.py:1:14 invalid-assignment: Object of type "
                '`Literal["text"]` is not assignable to `int`'
            ),
        ),
    )


def test_ty_preserves_the_order_of_multiple_findings() -> None:
    stdout = (
        "["
        + ",".join(
            TY_REPORT.strip()[1:-1].replace("example.py", filename)
            for filename in ("first.py", "second.py")
        )
        + "]"
    )

    result = read_ty(stdout=stdout, exit_code=1, stderr="")

    assert len(result.messages) == 2
    assert result.messages[0].startswith("first.py:1:14 invalid-assignment:")
    assert result.messages[1].startswith("second.py:1:14 invalid-assignment:")


@pytest.mark.parametrize(
    ("reported_path", "expected_path"),
    [
        ("src/app.py", "src/app.py"),
        ("./src/app.py", "src/app.py"),
        ("/workspace/project/src/app.py", "src/app.py"),
        ("/workspace/elsewhere.py", "../elsewhere.py"),
    ],
)
def test_ty_paths_are_relative_to_the_checked_project(
    reported_path: str,
    expected_path: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    stdout = TY_REPORT.replace("/workspace/project/example.py", reported_path)

    result = read_ty(stdout=stdout, exit_code=1, stderr="")

    assert result.messages[0].startswith(f"{expected_path}:1:14 invalid-assignment:")


def test_ty_paths_resolve_a_symlinked_project_root(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    link = tmp_path / "link"
    link.symlink_to(project, target_is_directory=True)
    stdout = TY_REPORT.replace(
        "/workspace/project/example.py", str(project / "example.py")
    )

    result = TyCommand().process_response(
        check_id="types",
        project_root=link,
        exit_code=1,
        stdout=stdout,
        stderr="",
    )

    assert result.messages[0].startswith("example.py:1:14 invalid-assignment:")


def test_only_fields_used_to_render_ty_findings_are_required() -> None:
    stdout = json.dumps(
        [
            {
                "description": "invalid-syntax: Expected an identifier",
                "location": {
                    "path": "app.py",
                    "positions": {"begin": {"line": 2, "column": 3}},
                },
            }
        ]
    )

    result = read_ty(stdout=stdout, exit_code=1, stderr="")

    assert result.messages == ("app.py:2:3 invalid-syntax: Expected an identifier",)


@pytest.mark.parametrize(
    "stdout",
    [
        "",
        "All checks passed!",
        "{}",
        "null",
        "[{}]",
        '[{"description": "error"}]',
        TY_REPORT.replace('"description":', '"message":'),
        TY_REPORT.replace('"path":', '"filename":'),
        TY_REPORT.replace('"positions":', '"lines":'),
        TY_REPORT.replace('"begin":', '"start":'),
        TY_REPORT.replace('"line":', '"row":'),
        TY_REPORT.replace('"column":', '"col":'),
        TY_REPORT.replace('"line": 1', '"line": "invalid"'),
    ],
)
def test_ty_unreadable_reports_explain_the_required_output_format(
    stdout: str,
) -> None:
    with pytest.raises(CheckOutputError) as raised:
        read_ty(stdout=stdout, exit_code=0, stderr="")

    assert raised.value.check_id == "types"
    assert raised.value.command is CommandName.TY
    assert "--output-format gitlab" in str(raised.value)
    assert raised.value.__cause__ is not None


@pytest.mark.parametrize("exit_code", [2, 3, -9])
def test_ty_process_failures_preserve_stdout_and_stderr(exit_code: int) -> None:
    with pytest.raises(CheckOutputError) as raised:
        read_ty(
            stdout=TY_REPORT,
            exit_code=exit_code,
            stderr="  Could not read the source file.\n",
        )

    assert raised.value.check_id == "types"
    assert raised.value.command is CommandName.TY
    assert f"ty exited {exit_code}" in str(raised.value)
    assert TY_REPORT in str(raised.value)
    assert "Could not read the source file." in str(raised.value)


@pytest.mark.parametrize(
    ("stdout", "stderr", "expected"),
    [
        ("stdout failure\n", "", "stdout failure"),
        ("", "stderr failure\n", "stderr failure"),
        ("", "", ""),
    ],
)
def test_ty_process_failures_handle_empty_output_streams(
    stdout: str,
    stderr: str,
    expected: str,
) -> None:
    with pytest.raises(CheckOutputError) as raised:
        read_ty(stdout=stdout, exit_code=2, stderr=stderr)

    assert raised.value.problem == expected


def test_a_ty_installation_failure_keeps_the_uvx_diagnostic() -> None:
    with pytest.raises(CheckOutputError) as raised:
        read_ty(
            stdout="",
            exit_code=1,
            stderr="Failed to download ty: connection refused.\n",
        )

    assert "Failed to download ty: connection refused." in str(raised.value)


def test_ty_exit_one_with_an_empty_report_is_an_error() -> None:
    with pytest.raises(CheckOutputError) as raised:
        read_ty(stdout="[]", exit_code=1, stderr="Unexpected failure\n")

    assert raised.value.check_id == "types"
    assert "ty exited 1 without reporting findings" in str(raised.value)
    assert "Unexpected failure" in str(raised.value)


def test_ty_runs_the_configured_arguments_under_a_custom_check_id(
    processes: FakeProcesses,
) -> None:
    processes.stdout = "[]"
    check = Check(
        id="custom-types",
        package_type=PackageType.UVX,
        package="ty==0.0.80",
        command=CommandName.TY,
        args=("check", "--output-format", "gitlab", "--error-on-warning", "src"),
    )

    result = TyCommand().run(check=check, project_root=PROJECT_ROOT)

    assert processes.started[0].argv == (
        "uvx",
        "--from",
        "ty==0.0.80",
        "ty",
        "check",
        "--output-format",
        "gitlab",
        "--error-on-warning",
        "src",
    )
    assert processes.started[0].cwd == PROJECT_ROOT
    assert result == CheckResult(
        check_id="custom-types", status=CheckStatus.PASSED, messages=()
    )
