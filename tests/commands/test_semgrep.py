"""Tests for :mod:`checksmith.commands.semgrep`."""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Final

import pytest

from checksmith.commands.semgrep import SemgrepCommand
from checksmith.config import Check
from checksmith.dtos import CheckResult, CheckStatus, CommandName, PackageType
from checksmith.errors import CheckOutputError
from tests.conftest import FakeProcesses

PROJECT_ROOT = Path("/workspace/project")


def test_importing_semgrep_does_not_load_other_command_implementations(
    imported_modules: Callable[[str], frozenset[str]],
) -> None:
    modules = imported_modules("checksmith.commands.semgrep")

    assert not modules & {
        "checksmith.commands.registry",
        "checksmith.commands.import_linter",
        "checksmith.commands.ruff",
        "checksmith.commands.ty",
    }


SEMGREP_REPORT: Final = """\
{
  "version": "1.176.1",
  "results": [
    {
      "check_id": "python-require-keyword-only-parameters",
      "path": "src/app.py",
      "start": {"line": 3, "col": 1, "offset": 12},
      "end": {"line": 4, "col": 9, "offset": 44},
      "extra": {
        "message": "Declare parameters after a bare *.",
        "severity": "ERROR",
        "metadata": {}
      }
    },
    {
      "check_id": "python-no-variadic-parameters",
      "path": "src/other.py",
      "start": {"line": 8, "col": 5},
      "extra": {"message": "Replace variadic parameters."}
    }
  ],
  "errors": [],
  "paths": {"scanned": ["src/app.py", "src/other.py"]}
}
"""


def read_semgrep(*, stdout: str, exit_code: int, stderr: str) -> CheckResult:
    return SemgrepCommand().process_response(
        check_id="function-style",
        project_root=PROJECT_ROOT,
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
    )


@pytest.mark.parametrize("exit_code", [0, 1])
def test_semgrep_without_findings_is_a_passing_check(exit_code: int) -> None:
    assert read_semgrep(
        stdout='{"results": [], "errors": []}',
        exit_code=exit_code,
        stderr="",
    ) == CheckResult(check_id="function-style", status=CheckStatus.PASSED, messages=())


@pytest.mark.parametrize("exit_code", [0, 1])
def test_semgrep_findings_fail_the_check_with_or_without_error_flag(
    exit_code: int,
) -> None:
    result = read_semgrep(stdout=SEMGREP_REPORT, exit_code=exit_code, stderr="")

    assert result == CheckResult(
        check_id="function-style",
        status=CheckStatus.FAILED,
        messages=(
            (
                "src/app.py:3:1 python-require-keyword-only-parameters "
                "Declare parameters after a bare *."
            ),
            (
                "src/other.py:8:5 python-no-variadic-parameters "
                "Replace variadic parameters."
            ),
        ),
    )


@pytest.mark.parametrize(
    ("reported_path", "expected_path"),
    [
        ("src/app.py", "src/app.py"),
        ("./src/app.py", "src/app.py"),
        ("/workspace/project/src/app.py", "src/app.py"),
        ("/workspace/elsewhere.py", "../elsewhere.py"),
    ],
)
def test_semgrep_paths_are_relative_to_the_scanned_project(
    reported_path: str,
    expected_path: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    stdout = json.dumps(
        {
            "results": [
                {
                    "check_id": "function-style",
                    "path": reported_path,
                    "start": {"line": 1, "col": 2},
                    "extra": {"message": "Use named parameters."},
                }
            ],
            "errors": [],
        }
    )

    result = read_semgrep(stdout=stdout, exit_code=0, stderr="")

    assert result.messages == (
        f"{expected_path}:1:2 function-style Use named parameters.",
    )


@pytest.mark.parametrize("exit_code", [2, 3, 4, 5, 7, -9])
def test_semgrep_process_failures_are_errors_even_with_findings(
    exit_code: int,
) -> None:
    with pytest.raises(CheckOutputError) as raised:
        read_semgrep(
            stdout=SEMGREP_REPORT,
            exit_code=exit_code,
            stderr="  Cannot load the rules configuration.\n",
        )

    assert raised.value.check_id == "function-style"
    assert raised.value.command is CommandName.SEMGREP
    assert f"semgrep exited {exit_code}" in str(raised.value)
    assert "Cannot load the rules configuration." in str(raised.value)


@pytest.mark.parametrize("exit_code", [0, 1])
@pytest.mark.parametrize("has_findings", [False, True])
def test_semgrep_scan_errors_prevent_a_completed_check(
    exit_code: int,
    has_findings: bool,
) -> None:
    stdout = (
        SEMGREP_REPORT if has_findings else '{"results": [], "errors": []}'
    ).replace(
        '"errors": []',
        '"errors": [{"message": "Could not parse src/broken.py", "code": 3}]',
    )

    with pytest.raises(CheckOutputError) as raised:
        read_semgrep(stdout=stdout, exit_code=exit_code, stderr="")

    assert raised.value.check_id == "function-style"
    assert raised.value.command is CommandName.SEMGREP
    assert "Could not parse src/broken.py" in str(raised.value)


@pytest.mark.parametrize(
    "stdout",
    [
        "Run completed without JSON output.",
        "[]",
        '{"results": []}',
        '{"errors": []}',
        '{"results": [{"check_id": "rule"}], "errors": []}',
        '{"results": [], "errors": [{}]}',
    ],
)
def test_semgrep_unreadable_reports_explain_the_required_json_format(
    stdout: str,
) -> None:
    with pytest.raises(CheckOutputError) as raised:
        read_semgrep(stdout=stdout, exit_code=0, stderr="")

    assert raised.value.check_id == "function-style"
    assert raised.value.command is CommandName.SEMGREP
    assert "--json" in str(raised.value)


def test_a_semgrep_installation_failure_keeps_the_uvx_diagnostic() -> None:
    with pytest.raises(CheckOutputError) as raised:
        read_semgrep(
            stdout="",
            exit_code=1,
            stderr="Failed to download semgrep: connection refused.\n",
        )

    assert "Failed to download semgrep: connection refused." in str(raised.value)


def test_semgrep_runs_through_uvx_with_the_configured_arguments(
    processes: FakeProcesses,
) -> None:
    processes.stdout = '{"results": [], "errors": []}'
    check = Check(
        id="function-style",
        package_type=PackageType.UVX,
        package="semgrep==1.176.1",
        command=CommandName.SEMGREP,
        args=("scan", "--config", ".checksmith/semgrep.yaml", "--json", "."),
    )

    result = SemgrepCommand().run(check=check, project_root=PROJECT_ROOT)

    assert processes.started[0].argv == (
        "uvx",
        "--from",
        "semgrep==1.176.1",
        "semgrep",
        "scan",
        "--config",
        ".checksmith/semgrep.yaml",
        "--json",
        ".",
    )
    assert processes.started[0].cwd == PROJECT_ROOT
    assert result == CheckResult(check_id="function-style", status=CheckStatus.PASSED, messages=())
