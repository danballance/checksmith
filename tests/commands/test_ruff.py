"""Tests for :mod:`checksmith.commands.ruff`."""

from collections.abc import Callable
from pathlib import Path
from typing import Final

import pytest

from checksmith.commands.ruff import RuffCommand
from checksmith.config import Check
from checksmith.dtos import CheckResult, CommandName, ErrorSeverity, PackageType
from checksmith.errors import CheckOutputError
from tests.conftest import FakeProcesses

PROJECT_ROOT = Path("/workspace/project")


def test_importing_ruff_does_not_load_other_command_implementations(
    imported_modules: Callable[[str], frozenset[str]],
) -> None:
    modules = imported_modules("checksmith.commands.ruff")

    assert not modules & {
        "checksmith.commands.registry",
        "checksmith.commands.semgrep",
    }


def ruff_check(*, check_id: str) -> Check:
    """One configured check, of the kind :class:`RuffCommand` handles."""
    return Check(
        id=check_id,
        package_type=PackageType.UVX,
        package="ruff==0.16.7",
        command=CommandName.RUFF,
        args=("check", "."),
    )


def test_a_failed_run_names_the_check_it_was_given_rather_than_the_command(
    processes: FakeProcesses,
) -> None:
    """One command serves many checks, so only the argument can say which failed."""
    command = RuffCommand()

    with pytest.raises(CheckOutputError) as raised:
        command.run(check=ruff_check(check_id="second-lint"), project_root=PROJECT_ROOT)

    assert "second-lint" in str(raised.value)


def test_a_clean_run_of_the_real_ruff_command_passes(
    processes: FakeProcesses,
) -> None:
    """Start to finish through the shipped command: a project with nothing wrong."""
    processes.stdout = "[]"
    command = RuffCommand()

    result = command.run(check=ruff_check(check_id="lint"), project_root=PROJECT_ROOT)

    assert result == CheckResult(check_id="lint", severity=None, messages=())


# Reading ruff's JSON

RUFF_REPORT: Final = """\
[
  {
    "cell": null,
    "code": "I001",
    "end_location": {"column": 10, "row": 1},
    "filename": "/workspace/project/src/app.py",
    "fix": {
      "applicability": "safe",
      "edits": [
        {
          "content": "import os\\n\\n",
          "end_location": {"column": 1, "row": 2},
          "location": {"column": 1, "row": 1}
        }
      ],
      "message": "Organize imports"
    },
    "location": {"column": 1, "row": 1},
    "message": "Import block is un-sorted or un-formatted",
    "name": "unsorted-imports",
    "noqa_row": 1,
    "severity": "error",
    "url": "https://docs.astral.sh/ruff/rules/unsorted-imports"
  },
  {
    "cell": null,
    "code": "F401",
    "end_location": {"column": 10, "row": 1},
    "filename": "/workspace/project/src/app.py",
    "fix": null,
    "location": {"column": 8, "row": 1},
    "message": "`os` imported but unused",
    "name": "unused-import",
    "noqa_row": 1,
    "severity": "error",
    "url": "https://docs.astral.sh/ruff/rules/unused-import"
  }
]
"""
"""Ruff 0.16.7's own output, every key it writes left where it wrote it."""

RUFF_TEXT_REPORT: Final = """\
F401 [*] `os` imported but unused
 --> src/app.py:1:8
  |
1 | import os
  |        ^^
  |
help: Remove unused import: `os`

Found 1 error.
"""
"""What ruff writes when the check's args did not ask for JSON."""


def read_ruff(*, stdout: str, exit_code: int, stderr: str) -> CheckResult:
    """Hand one ruff report to the adapter, exactly as ``run`` would."""
    return RuffCommand().process_response(
        check_id="lint",
        project_root=PROJECT_ROOT,
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
    )


def test_a_clean_report_is_a_passing_check() -> None:
    """Ruff having found nothing is an empty array, and exit zero."""
    assert read_ruff(stdout="[]", exit_code=0, stderr="") == CheckResult(
        check_id="lint",
        severity=None,
        messages=(),
    )


def test_each_diagnostic_becomes_a_finding_against_the_project_root() -> None:
    """Ruff reports absolute paths; a user thinks in paths from their project."""
    result = read_ruff(stdout=RUFF_REPORT, exit_code=1, stderr="")

    assert result.messages == (
        "src/app.py:1:1 I001 Import block is un-sorted or un-formatted",
        "src/app.py:1:8 F401 `os` imported but unused",
    )


def test_a_report_with_diagnostics_is_a_failing_check() -> None:
    """The findings are the report, so having any of them is the failure."""
    assert read_ruff(stdout=RUFF_REPORT, exit_code=1, stderr="").severity is ErrorSeverity.FAILURE


def test_a_diagnostic_that_names_no_rule_omits_the_code() -> None:
    """``code`` is null for a diagnostic that is not a rule. ``None`` must not print."""
    stdout = """\
    [
      {
        "code": null,
        "filename": "/workspace/project/src/app.py",
        "location": {"column": 5, "row": 1},
        "message": "Expected an identifier"
      }
    ]
    """

    result = read_ruff(stdout=stdout, exit_code=1, stderr="")

    assert result.messages == ("src/app.py:1:5 Expected an identifier",)


def test_only_the_fields_checksmith_reads_are_required() -> None:
    """A release that stops writing ``noqa_row`` must not take the gate down."""
    stdout = """\
    [
      {
        "code": "F401",
        "filename": "/workspace/project/src/app.py",
        "location": {"column": 8, "row": 1},
        "message": "`os` imported but unused"
      }
    ]
    """

    result = read_ruff(stdout=stdout, exit_code=1, stderr="")

    assert result.messages == ("src/app.py:1:8 F401 `os` imported but unused",)


def test_a_diagnostic_missing_a_field_checksmith_reads_is_refused() -> None:
    """Ignoring the unread keys is not the same as inventing a missing one."""
    stdout = """\
    [
      {
        "code": "F401",
        "filename": "/workspace/project/src/app.py",
        "location": {"column": 8, "row": 1}
      }
    ]
    """

    with pytest.raises(CheckOutputError) as raised:
        read_ruff(stdout=stdout, exit_code=1, stderr="")

    assert "message" in str(raised.value)


def test_a_file_outside_the_project_root_still_reads_as_a_path() -> None:
    """A check may be pointed anywhere; saying where is better than refusing to."""
    stdout = """\
    [
      {
        "code": "F401",
        "filename": "/workspace/elsewhere/app.py",
        "location": {"column": 8, "row": 1},
        "message": "`os` imported but unused"
      }
    ]
    """

    result = read_ruff(stdout=stdout, exit_code=1, stderr="")

    assert result.messages == (
        "../elsewhere/app.py:1:8 F401 `os` imported but unused",
    )


def test_ruff_failing_is_not_reported_as_a_coding_standard_violation() -> None:
    """Exit 2 is ruff broken --- an unreadable ``ruff.toml``, not unreadable code."""
    stderr = "ruff failed\n  Cause: unknown field `nonsense_key`\n"

    with pytest.raises(CheckOutputError) as raised:
        read_ruff(stdout="", exit_code=2, stderr=stderr)

    assert str(raised.value) == (
        "Check 'lint': ruff exited 2 rather than reporting findings:\n"
        "ruff failed\n"
        "  Cause: unknown field `nonsense_key`"
    )


def test_output_that_is_not_json_names_the_switch_the_args_need() -> None:
    """The config owns the invocation, so the config author is who can fix this."""
    with pytest.raises(CheckOutputError) as raised:
        read_ruff(stdout=RUFF_TEXT_REPORT, exit_code=1, stderr="")

    assert "lint" in str(raised.value)
    assert "--output-format json" in str(raised.value)


def test_json_of_another_shape_is_refused() -> None:
    """``--output-format sarif`` parses as JSON and is still not what this reads."""
    with pytest.raises(CheckOutputError) as raised:
        read_ruff(stdout='{"version": "2.1.0", "runs": []}', exit_code=1, stderr="")

    assert "--output-format json" in str(raised.value)
