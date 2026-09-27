from collections.abc import Callable
from pathlib import Path
from typing import Final

import pytest

from checksmith.commands.registry import CommandFactory
from checksmith.config import Check
from checksmith.dtos import CheckResult, CheckStatus, CommandName, PackageType
from checksmith.errors import CheckOutputError
from tests.conftest import FakeProcesses, make_command_factory

PROJECT_ROOT: Final = Path("/workspace/project")
ADVISORY_HEADER: Final = (
    "The following ignore comment(s) are no longer necessary "
    "(complexity is within the allowed limit) and can be removed:"
)
ADVISORY: Final = (
    f"\n{ADVISORY_HEADER}\n"
    "src/app.py:3  function=parse complexity=2  # complexipy: ignore\n"
    "src/other.py:10  function=Other::method complexity=0  # noqa: complexipy\n"
)


def read_complexipy(*, stdout: str, exit_code: int, stderr: str) -> CheckResult:
    return (
        make_command_factory(executor=FakeProcesses())
        .for_name(name=CommandName.COMPLEXIPY)
        .process_response(
            check_id="complexity",
            project_root=PROJECT_ROOT,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
        )
    )


def test_importing_complexipy_does_not_load_other_commands(
    imported_modules: Callable[[str], frozenset[str]],
) -> None:
    modules = imported_modules("checksmith.commands.complexipy")

    assert not modules & {
        "checksmith.commands.registry",
        "checksmith.commands.ruff",
        "checksmith.commands.ty",
        "checksmith.commands.pytest",
    }


@pytest.mark.parametrize("stdout", ["", "\n \t\n", ADVISORY])
def test_complexipy_without_findings_passes(stdout: str) -> None:
    assert read_complexipy(stdout=stdout, exit_code=0, stderr="") == CheckResult(
        check_id="complexity", status=CheckStatus.PASSED, messages=()
    )


@pytest.mark.parametrize("exit_code", [0, 1])
@pytest.mark.parametrize("advisory", ["", ADVISORY])
def test_complexipy_findings_fail_and_preserve_order(
    exit_code: int, advisory: str
) -> None:
    result = read_complexipy(
        stdout=(
            "src/app.py parse 11\n"
            "src/a file.py Parser::méthode 12\n"
            "src/script.py <module> 13\n"
            f"{advisory}"
        ),
        exit_code=exit_code,
        stderr="",
    )

    assert result == CheckResult(
        check_id="complexity",
        status=CheckStatus.FAILED,
        messages=(
            "src/app.py: parse has cognitive complexity 11.",
            "src/a file.py: Parser::méthode has cognitive complexity 12.",
            "src/script.py: <module> has cognitive complexity 13.",
        ),
    )


@pytest.mark.parametrize(
    ("reported_path", "expected_path"),
    [
        ("src/app.py", "src/app.py"),
        ("./src/app.py", "src/app.py"),
        ("src/../app.py", "app.py"),
        ("/workspace/project/src/app.py", "src/app.py"),
        ("/workspace/other.py", "../other.py"),
        ("../other.py", "../other.py"),
        ("src/a file.py", "src/a file.py"),
        ("  app.py", "  app.py"),
    ],
)
def test_complexipy_paths_are_relative_to_the_checked_project(
    reported_path: str,
    expected_path: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)

    result = read_complexipy(
        stdout=f"{reported_path} parse 11\n", exit_code=1, stderr=""
    )

    assert result.messages == (f"{expected_path}: parse has cognitive complexity 11.",)


def test_complexipy_resolves_a_symlinked_project_root(
    tmp_path: Path, command_factory: CommandFactory
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    link = tmp_path / "link"
    link.symlink_to(project, target_is_directory=True)

    result = command_factory.for_name(name=CommandName.COMPLEXIPY).process_response(
        check_id="complexity",
        project_root=link,
        exit_code=1,
        stdout=f"{project / 'app.py'} parse 11\n",
        stderr="",
    )

    assert result.messages == ("app.py: parse has cognitive complexity 11.",)


@pytest.mark.parametrize(
    "stdout",
    [
        "Analysis completed!",
        "[]",
        "app.py parse",
        "parse 11",
        "app.py parse -1",
        "app.py parse +1",
        "app.py parse 1.0",
        "app.py parse 1_000",
        "app.py parse ١١",
        "app\0.py parse 11",
        "app.py parse 11\nunexpected output",
        "Results saved at /tmp/complexipy-results.json\napp.py parse 11",
        (
            "app.py parse 11\nerror: Failed to process broken.py - "
            "Please check file/folder exists or check syntax"
        ),
        (
            "app.py parse 11\n\x1b[31merror\x1b[0m: Failed to process broken.py - "
            "Please check file/folder exists or check syntax"
        ),
        ADVISORY_HEADER,
        ADVISORY + "app.py parse 11\n",
        ADVISORY + ADVISORY,
        ADVISORY.replace("function=parse", "function="),
        ADVISORY.replace("complexity=2", "complexity=-2"),
        ADVISORY.replace("app.py:3", "app.py:0"),
        ADVISORY.replace("# complexipy: ignore", "# something else"),
        ADVISORY.replace(f"\n{ADVISORY_HEADER}\n", ""),
        ADVISORY + "error: Failed to process broken.py - "
        "Please check file/folder exists or check syntax\n",
    ],
)
def test_complexipy_rejects_malformed_or_partial_reports(stdout: str) -> None:
    with pytest.raises(CheckOutputError) as raised:
        read_complexipy(stdout=stdout, exit_code=1, stderr="process context\n")

    assert raised.value.check_id == "complexity"
    assert raised.value.command is CommandName.COMPLEXIPY
    assert "--plain --failed" in str(raised.value)
    assert stdout.strip() in raised.value.problem
    assert "process context" in raised.value.problem
    assert raised.value.__cause__ is not None


@pytest.mark.parametrize("exit_code", [0, 1])
@pytest.mark.parametrize(
    "stderr",
    [
        "Failed to parse /workspace/project/complexipy.toml: invalid TOML\n",
        "Failed to parse /workspace/project/.complexipy.toml: invalid TOML\n",
        "Failed to parse /workspace/project/pyproject.toml: invalid TOML\n",
        "Invalid config in /workspace/project/pyproject.toml: invalid type\n",
    ],
)
def test_complexipy_reports_configuration_errors_even_with_findings(
    exit_code: int, stderr: str
) -> None:
    with pytest.raises(
        CheckOutputError, match="reported configuration errors"
    ) as raised:
        read_complexipy(stdout="app.py parse 11\n", exit_code=exit_code, stderr=stderr)

    assert stderr.strip() in raised.value.problem
    assert "app.py parse 11" in raised.value.problem


@pytest.mark.parametrize("exit_code", [2, 3, -9])
def test_complexipy_abnormal_exits_preserve_both_streams(exit_code: int) -> None:
    with pytest.raises(
        CheckOutputError, match=f"complexipy exited {exit_code}"
    ) as raised:
        read_complexipy(
            stdout="app.py parse 11\n",
            exit_code=exit_code,
            stderr="  command failed\n",
        )

    assert raised.value.problem == "app.py parse 11\ncommand failed"


@pytest.mark.parametrize("stdout", ["", ADVISORY])
def test_complexipy_exit_one_without_findings_is_an_error(stdout: str) -> None:
    with pytest.raises(CheckOutputError, match="without reporting findings") as raised:
        read_complexipy(
            stdout=stdout,
            exit_code=1,
            stderr="Failed to download complexipy: connection refused.\n",
        )

    assert "Failed to download complexipy: connection refused." in raised.value.problem


def test_complexipy_allows_uvx_progress_on_stderr() -> None:
    result = read_complexipy(
        stdout="app.py parse 11\n",
        exit_code=1,
        stderr="Downloading complexipy\nInstalled 1 package in 8ms\n",
    )

    assert result.status is CheckStatus.FAILED


@pytest.mark.parametrize(
    "args",
    [
        ("--plain", "--failed", "src"),
        ("--plain=true", "--failed=true", "src"),
        ("--failed", "--plain=true", "--", "--plain=false"),
    ],
)
def test_complexipy_runs_the_unchanged_configured_arguments(
    args: tuple[str, ...],
    processes: FakeProcesses,
    command_factory: CommandFactory,
) -> None:
    check = Check(
        id="custom-complexity",
        package_type=PackageType.UVX,
        package="complexipy==8.0.1",
        command=CommandName.COMPLEXIPY,
        args=args,
    )

    result = command_factory.for_name(name=CommandName.COMPLEXIPY).run(
        check=check, project_root=PROJECT_ROOT
    )

    assert processes.started[0].argv == (
        "uvx",
        "--from",
        "complexipy==8.0.1",
        "complexipy",
        *args,
    )
    assert processes.started[0].cwd == PROJECT_ROOT
    assert result == CheckResult(
        check_id="custom-complexity", status=CheckStatus.PASSED, messages=()
    )


@pytest.mark.parametrize(
    "args",
    [
        (".",),
        ("--plain", "."),
        ("--failed", "."),
        ("--plain=false", "--failed", "."),
        ("--plain", "--failed=false", "."),
        ("--plain=TRUE", "--failed", "."),
        ("--plain", "--failed", "--failed=false", "."),
        ("--plain", "--plain", "--failed", "."),
        ("--", "--plain", "--failed"),
        ("--plain", "--", "--failed"),
    ],
)
def test_complexipy_rejects_missing_or_disabled_output_flags_before_execution(
    args: tuple[str, ...],
    processes: FakeProcesses,
    command_factory: CommandFactory,
) -> None:
    check = Check(
        id="complexity",
        package_type=PackageType.UVX,
        package="complexipy==8.0.1",
        command=CommandName.COMPLEXIPY,
        args=args,
    )

    with pytest.raises(CheckOutputError, match="--plain --failed"):
        command_factory.for_name(name=CommandName.COMPLEXIPY).run(
            check=check, project_root=PROJECT_ROOT
        )

    assert processes.started == []
