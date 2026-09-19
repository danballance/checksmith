"""Tests for :mod:`checksmith.commands.command`."""

import json
import subprocess
from pathlib import Path
from typing import Final

import pytest
from pydantic import BaseModel, ConfigDict

from checksmith.commands.command import (
    Command,
    RuffCommand,
    SemgrepCommand,
)
from checksmith.config import Check
from checksmith.dtos import CheckResult, CommandName, ErrorSeverity, PackageType
from checksmith.errors import CheckExecutionError, CheckOutputError
from tests.conftest import FakeProcesses

PROJECT_ROOT = Path("/workspace/project")


def ruff_check(*, check_id: str = "lint") -> Check:
    """One configured check, of the kind :class:`RuffCommand` handles."""
    return Check(
        id=check_id,
        package_type=PackageType.UVX,
        package="ruff==0.16.7",
        command=CommandName.RUFF,
        args=("check", "."),
    )


class ProcessOutput(BaseModel):
    """Everything one call to ``process_response`` was handed."""

    model_config = ConfigDict(frozen=True)

    check_id: str
    project_root: Path
    exit_code: int
    stdout: str
    stderr: str


class RecordingCommand(Command):
    """A command that records the process output it was asked to read.

    Every ``process_response`` Checksmith ships is still a stub, so this is the
    only way to watch what ``run`` hands across that seam.
    """

    def __init__(self) -> None:
        self.read: list[ProcessOutput] = []

    @property
    def name(self) -> CommandName:
        return CommandName.RUFF

    def process_response(
        self,
        *,
        check_id: str,
        project_root: Path,
        exit_code: int,
        stdout: str,
        stderr: str,
    ) -> CheckResult:
        self.read.append(
            ProcessOutput(
                check_id=check_id,
                project_root=project_root,
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
            )
        )
        return CheckResult(
            check_id=check_id,
            severity=ErrorSeverity.FAILURE if exit_code != 0 else None,
            messages=("read by the recorder",),
        )


def test_the_base_command_cannot_be_instantiated() -> None:
    """An ABC, so a program with no implementation is an error, never a silent pass."""
    with pytest.raises(TypeError):
        Command()  # type: ignore[abstract]


@pytest.mark.parametrize(
    ("command_name", "expected"),
    [
        (CommandName.RUFF, RuffCommand),
        (CommandName.SEMGREP, SemgrepCommand),
    ],
)
def test_the_configured_command_selects_the_class_that_handles_it(
    command_name: CommandName,
    expected: type[Command],
) -> None:
    """The ``command`` field is the whole of the dispatch; nothing else selects."""
    command = Command.for_name(name=command_name)

    assert isinstance(command, expected)
    assert command.name is command_name


def test_the_registry_covers_every_command_a_config_may_name() -> None:
    """Total by construction, so a check's ``command`` can never miss."""
    registry = Command.registry()

    assert set(registry) == set(CommandName)


def test_each_registered_command_answers_to_the_key_it_is_filed_under() -> None:
    """A command filed under the wrong key would run the wrong program."""
    registry = Command.registry()

    assert all(command.name is name for name, command in registry.items())


@pytest.mark.parametrize("command_name", list(CommandName))
def test_a_command_holds_no_configuration_of_its_own(
    command_name: CommandName,
) -> None:
    """Statelessness is the point: one instance serves every check that names it."""
    command = Command.for_name(name=command_name)

    assert vars(command) == {}


# Starting the process


def test_a_check_is_started_as_the_vector_it_resolves_to(
    processes: FakeProcesses,
) -> None:
    """A vector, never a command string: nothing reaches a shell to re-parse it."""
    RecordingCommand().run(check=ruff_check(), project_root=PROJECT_ROOT)

    assert processes.started[0].argv == (
        "uvx",
        "--from",
        "ruff==0.16.7",
        "ruff",
        "check",
        ".",
    )


def test_a_check_is_started_in_the_project_root_it_was_given(
    processes: FakeProcesses,
) -> None:
    """The tool runs where the project is, not where the user happened to stand."""
    RecordingCommand().run(check=ruff_check(), project_root=PROJECT_ROOT)

    assert processes.started[0].cwd == PROJECT_ROOT


def test_stdin_is_closed_so_a_tool_that_asks_a_question_cannot_hang(
    processes: FakeProcesses,
) -> None:
    """Nobody is watching a quality gate; a prompt must fail rather than wait."""
    RecordingCommand().run(check=ruff_check(), project_root=PROJECT_ROOT)

    assert processes.started[0].stdin == subprocess.DEVNULL


def test_a_program_that_cannot_be_started_names_the_check_that_wanted_it(
    processes: FakeProcesses,
) -> None:
    """An absent ``uvx`` says which program; only Checksmith can say which check."""
    processes.refusal = FileNotFoundError(2, "No such file or directory", "uvx")

    with pytest.raises(CheckExecutionError) as raised:
        RecordingCommand().run(check=ruff_check(), project_root=PROJECT_ROOT)

    assert raised.value.check_id == "lint"
    assert raised.value.program == "uvx"
    assert raised.value.working_directory == PROJECT_ROOT
    assert "uvx" in str(raised.value)
    assert "lint" in str(raised.value)


def test_nothing_is_read_when_nothing_ran(processes: FakeProcesses) -> None:
    """Reading the output of a process that never started would invent a result."""
    processes.refusal = FileNotFoundError(2, "No such file or directory", "uvx")
    command = RecordingCommand()

    with pytest.raises(CheckExecutionError):
        command.run(check=ruff_check(), project_root=PROJECT_ROOT)

    assert command.read == []


@pytest.mark.parametrize(
    ("command_name", "package"),
    [
        (CommandName.RUFF, "ruff==0.16.7"),
        (CommandName.SEMGREP, "semgrep==1.176.1"),
    ],
)
def test_invalid_utf8_output_is_a_check_output_error(
    command_name: CommandName,
    package: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    decoding_error = UnicodeDecodeError(
        "utf-8", b"\xff", 0, 1, "invalid start byte"
    )

    def refuse_decoding(
        argv: tuple[str, ...],
        *,
        cwd: str,
        stdin: int,
        capture_output: bool,
        text: bool,
        encoding: str,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        raise decoding_error

    monkeypatch.setattr(subprocess, "run", refuse_decoding)
    check = Check(
        id="encoding-check",
        package_type=PackageType.UVX,
        package=package,
        command=command_name,
        args=(),
    )

    with pytest.raises(CheckOutputError) as raised:
        Command.for_name(name=command_name).run(
            check=check, project_root=PROJECT_ROOT
        )

    assert raised.value.check_id == "encoding-check"
    assert raised.value.command is command_name
    assert raised.value.__cause__ is decoding_error
    assert str(decoding_error) in str(raised.value)
    assert f"{command_name.value} did not return UTF-8 output" in str(raised.value)


# Reading what it returned


def test_what_the_process_returned_is_handed_to_the_code_that_reads_it(
    processes: FakeProcesses,
) -> None:
    """The whole of what a process leaves behind, under the id that asked for it."""
    processes.exit_code = 1
    processes.stdout = "ruff said something"
    processes.stderr = "and grumbled"
    command = RecordingCommand()

    command.run(check=ruff_check(), project_root=PROJECT_ROOT)

    assert command.read == [
        ProcessOutput(
            check_id="lint",
            project_root=PROJECT_ROOT,
            exit_code=1,
            stdout="ruff said something",
            stderr="and grumbled",
        )
    ]


def test_a_nonzero_exit_is_read_rather_than_raised(
    processes: FakeProcesses,
) -> None:
    """A linter exits nonzero for the ordinary reason that it found something."""
    processes.exit_code = 1
    command = RecordingCommand()

    result = command.run(check=ruff_check(), project_root=PROJECT_ROOT)

    assert result.severity is ErrorSeverity.FAILURE


def test_the_result_of_reading_the_output_is_the_result_of_the_run(
    processes: FakeProcesses,
) -> None:
    """``run`` starts and collects; judging what came back is not its job."""
    result = RecordingCommand().run(check=ruff_check(), project_root=PROJECT_ROOT)

    assert result == CheckResult(
        check_id="lint",
        severity=None,
        messages=("read by the recorder",),
    )


def test_the_project_root_is_handed_to_the_code_that_reads_the_output(
    processes: FakeProcesses,
) -> None:
    """A tool reporting absolute paths needs something to be read against."""
    command = RecordingCommand()

    command.run(check=ruff_check(), project_root=PROJECT_ROOT)

    assert command.read[0].project_root == PROJECT_ROOT


def test_a_failed_run_names_the_check_it_was_given_rather_than_the_command(
    processes: FakeProcesses,
) -> None:
    """One command serves many checks, so only the argument can say which failed."""
    command = Command.for_name(name=CommandName.RUFF)

    with pytest.raises(CheckOutputError) as raised:
        command.run(check=ruff_check(check_id="second-lint"), project_root=PROJECT_ROOT)

    assert "second-lint" in str(raised.value)


def test_a_clean_run_of_the_real_ruff_command_passes(
    processes: FakeProcesses,
) -> None:
    """Start to finish through the shipped command: a project with nothing wrong."""
    processes.stdout = "[]"
    command = Command.for_name(name=CommandName.RUFF)

    result = command.run(check=ruff_check(), project_root=PROJECT_ROOT)

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


def read_ruff(*, stdout: str, exit_code: int, stderr: str = "") -> CheckResult:
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
    assert read_ruff(stdout="[]", exit_code=0) == CheckResult(
        check_id="lint",
        severity=None,
        messages=(),
    )


def test_each_diagnostic_becomes_a_finding_against_the_project_root() -> None:
    """Ruff reports absolute paths; a user thinks in paths from their project."""
    result = read_ruff(stdout=RUFF_REPORT, exit_code=1)

    assert result.messages == (
        "src/app.py:1:1 I001 Import block is un-sorted or un-formatted",
        "src/app.py:1:8 F401 `os` imported but unused",
    )


def test_a_report_with_diagnostics_is_a_failing_check() -> None:
    """The findings are the report, so having any of them is the failure."""
    assert read_ruff(stdout=RUFF_REPORT, exit_code=1).severity is ErrorSeverity.FAILURE


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

    result = read_ruff(stdout=stdout, exit_code=1)

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

    result = read_ruff(stdout=stdout, exit_code=1)

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
        read_ruff(stdout=stdout, exit_code=1)

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

    result = read_ruff(stdout=stdout, exit_code=1)

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
        read_ruff(stdout=RUFF_TEXT_REPORT, exit_code=1)

    assert "lint" in str(raised.value)
    assert "--output-format json" in str(raised.value)


def test_json_of_another_shape_is_refused() -> None:
    """``--output-format sarif`` parses as JSON and is still not what this reads."""
    with pytest.raises(CheckOutputError) as raised:
        read_ruff(stdout='{"version": "2.1.0", "runs": []}', exit_code=1)

    assert "--output-format json" in str(raised.value)


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
    ) == CheckResult(check_id="function-style", severity=None, messages=())


@pytest.mark.parametrize("exit_code", [0, 1])
def test_semgrep_findings_fail_the_check_with_or_without_error_flag(
    exit_code: int,
) -> None:
    result = read_semgrep(stdout=SEMGREP_REPORT, exit_code=exit_code, stderr="")

    assert result == CheckResult(
        check_id="function-style",
        severity=ErrorSeverity.FAILURE,
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
    assert result == CheckResult(check_id="function-style", severity=None, messages=())
