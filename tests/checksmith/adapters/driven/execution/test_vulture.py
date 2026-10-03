import os
from collections.abc import Callable
from pathlib import Path
from typing import Final

import pytest

from checksmith.adapters.driven.execution.prerequisites import (
    LocalProjectFiles,
    UvProjectPrerequisites,
)
from checksmith.adapters.driven.execution.registry import CommandFactory
from checksmith.adapters.driven.execution.vulture import VultureCommand
from checksmith.domain.config import Argument, Check, ConfigPath
from checksmith.domain.errors import CheckOutputError, CheckPrerequisiteError
from checksmith.domain.models import CheckResult, CheckStatus, CommandName, PackageType
from tests.conftest import FakeProcesses, make_command_factory

PROJECT_ROOT: Final = Path("/workspace/project")

# Vulture 2.16's report, run from the project root over one module holding every
# kind of finding it makes.
VULTURE_REPORT: Final = """\
src/my pkg/module.py:1: unused import 'os' (90% confidence)
src/my pkg/module.py:4: unused class 'Unused' (60% confidence)
src/my pkg/module.py:5: unused variable 'attribute' (60% confidence)
src/my pkg/module.py:8: unused attribute 'instance' (60% confidence)
src/my pkg/module.py:10: unused method 'method' (60% confidence)
src/my pkg/module.py:13: unused property 'prop' (60% confidence)
src/my pkg/module.py:18: unused function 'function' (60% confidence)
src/my pkg/module.py:18: unused variable 'argument' (100% confidence)
src/my pkg/module.py:19: unused variable 'value' (60% confidence)
src/my pkg/module.py:21: unreachable code after 'return' (100% confidence)
src/my pkg/module.py:24: unused function 'conditions' (60% confidence)
src/my pkg/module.py:28: unreachable 'else' block (100% confidence)
src/my pkg/module.py:29: redundant if-condition (100% confidence)
src/my pkg/module.py:31: unsatisfiable 'while' condition (100% confidence)
src/my pkg/module.py:33: unsatisfiable 'ternary' condition (100% confidence)
"""
SYNTAX_ERROR: Final = 'broken/bad.py:1: invalid syntax at "def broken(:"\n'


def vulture_check(*, args: tuple[Argument, ...]) -> Check:
    return Check(
        id="dead-code",
        package_type=PackageType.UVX,
        package="vulture==2.16",
        command=CommandName.VULTURE,
        args=args,
    )


def read_vulture(*, stdout: str, exit_code: int, stderr: str) -> CheckResult:
    return (
        make_command_factory(executor=FakeProcesses())
        .for_name(name=CommandName.VULTURE)
        .process_response(
            check_id="dead-code",
            project_root=PROJECT_ROOT,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
        )
    )


def test_importing_vulture_does_not_load_other_command_implementations(
    imported_modules: Callable[[str], frozenset[str]],
) -> None:
    modules = imported_modules("checksmith.adapters.driven.execution.vulture")

    assert not modules & {
        "checksmith.adapters.driven.execution.registry",
        "checksmith.adapters.driven.execution.import_linter",
        "checksmith.adapters.driven.execution.ruff",
        "checksmith.adapters.driven.execution.semgrep",
        "checksmith.adapters.driven.execution.ty",
    }


@pytest.mark.parametrize("stderr", ["", "Installed 1 package in 3ms\n"])
def test_vulture_without_findings_is_a_passing_check(stderr: str) -> None:
    assert read_vulture(stdout="", exit_code=0, stderr=stderr) == CheckResult(
        check_id="dead-code", status=CheckStatus.PASSED, messages=()
    )


def test_vulture_findings_fail_in_the_order_reported() -> None:
    result = read_vulture(stdout=VULTURE_REPORT, exit_code=3, stderr="")

    assert result == CheckResult(
        check_id="dead-code",
        status=CheckStatus.FAILED,
        messages=(
            "src/my pkg/module.py:1 unused import 'os' (90% confidence)",
            "src/my pkg/module.py:4 unused class 'Unused' (60% confidence)",
            "src/my pkg/module.py:5 unused variable 'attribute' (60% confidence)",
            "src/my pkg/module.py:8 unused attribute 'instance' (60% confidence)",
            "src/my pkg/module.py:10 unused method 'method' (60% confidence)",
            "src/my pkg/module.py:13 unused property 'prop' (60% confidence)",
            "src/my pkg/module.py:18 unused function 'function' (60% confidence)",
            "src/my pkg/module.py:18 unused variable 'argument' (100% confidence)",
            "src/my pkg/module.py:19 unused variable 'value' (60% confidence)",
            "src/my pkg/module.py:21 unreachable code after 'return' (100% confidence)",
            "src/my pkg/module.py:24 unused function 'conditions' (60% confidence)",
            "src/my pkg/module.py:28 unreachable 'else' block (100% confidence)",
            "src/my pkg/module.py:29 redundant if-condition (100% confidence)",
            "src/my pkg/module.py:31 unsatisfiable 'while' condition (100% confidence)",
            "src/my pkg/module.py:33 unsatisfiable 'ternary' condition (100% confidence)",
        ),
    )


def test_dead_code_found_beside_unparseable_input_still_fails_the_check() -> None:
    """Vulture exits 3 rather than 1 once it reports dead code, so the input
    error only surfaces after the findings are fixed."""
    result = read_vulture(stdout=VULTURE_REPORT, exit_code=3, stderr=SYNTAX_ERROR)

    assert result.status is CheckStatus.FAILED
    assert len(result.messages) == len(VULTURE_REPORT.splitlines())


@pytest.mark.parametrize(
    ("reported_path", "expected_path"),
    [
        ("src/app.py", "src/app.py"),
        ("./src/app.py", "src/app.py"),
        ("/workspace/project/src/app.py", "src/app.py"),
        ("/workspace/elsewhere.py", "../elsewhere.py"),
        ("src/odd:1: name.py", "src/odd:1: name.py"),
    ],
)
def test_vulture_paths_are_relative_to_the_checked_project(
    reported_path: str,
    expected_path: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)

    result = read_vulture(
        stdout=f"{reported_path}:7: unused function 'helper' (60% confidence)\n",
        exit_code=3,
        stderr="",
    )

    assert result.messages == (
        f"{expected_path}:7 unused function 'helper' (60% confidence)",
    )


def test_vulture_paths_resolve_a_symlinked_project_root(
    tmp_path: Path, command_factory: CommandFactory
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    link = tmp_path / "link"
    link.symlink_to(project, target_is_directory=True)

    result = command_factory.for_name(name=CommandName.VULTURE).process_response(
        check_id="dead-code",
        project_root=link,
        exit_code=3,
        stdout=f"{project / 'app.py'}:7: unused function 'helper' (60% confidence)\n",
        stderr="",
    )

    assert result.messages == ("app.py:7 unused function 'helper' (60% confidence)",)


@pytest.mark.parametrize(
    "stdout",
    [
        "All checks passed!",
        "src/app.py:1: unused import 'os' (90% confidence, 1 line)",
        "os  # unused import (src/app.py:1)",
        "# unreachable code after 'return' (src/app.py:21)",
        "Scanning: /workspace/project/src/app.py",
        "Reading configuration from /workspace/project/pyproject.toml",
        "src/app.py: unused import 'os' (90% confidence)",
        "src/app.py:0: unused import 'os' (90% confidence)",
        "src/app.py:1:  (90% confidence)",
        "src/app.py:1: unused import 'os' (101% confidence)",
        "src/a\0pp.py:1: unused import 'os' (90% confidence)",
        "src/app.py:1: unused import 'os' (90% confidence)\nScanning: src/app.py",
    ],
)
def test_vulture_unreadable_reports_explain_the_required_output_format(
    stdout: str,
) -> None:
    with pytest.raises(CheckOutputError) as raised:
        read_vulture(stdout=stdout, exit_code=3, stderr="")

    assert raised.value.check_id == "dead-code"
    assert raised.value.command is CommandName.VULTURE
    assert "--make-whitelist, --sort-by-size or --verbose" in str(raised.value)
    assert raised.value.__cause__ is not None


@pytest.mark.parametrize("exit_code", [1, 2, 4, -9])
def test_vulture_process_failures_preserve_stdout_and_stderr(exit_code: int) -> None:
    with pytest.raises(CheckOutputError) as raised:
        read_vulture(
            stdout=VULTURE_REPORT, exit_code=exit_code, stderr="Unexpected failure\n"
        )

    assert raised.value.check_id == "dead-code"
    assert raised.value.command is CommandName.VULTURE
    assert f"vulture exited {exit_code} rather than reporting findings" in str(
        raised.value
    )
    assert VULTURE_REPORT.strip() in str(raised.value)
    assert "Unexpected failure" in str(raised.value)


@pytest.mark.parametrize(
    ("exit_code", "stderr"),
    [
        (1, SYNTAX_ERROR),
        (1, "Error: /workspace/project/missing could not be found.\n"),
        (2, "Unknown configuration key: unknown\n"),
        (
            2,
            (
                "usage: vulture [options] [PATH ...]\n"
                "vulture: error: unrecognized arguments: --colour\n"
            ),
        ),
        (1, "Failed to download vulture: connection refused.\n"),
    ],
)
def test_vulture_input_and_usage_errors_keep_its_diagnostic(
    exit_code: int, stderr: str
) -> None:
    with pytest.raises(CheckOutputError) as raised:
        read_vulture(stdout="", exit_code=exit_code, stderr=stderr)

    assert raised.value.problem == stderr.strip()


@pytest.mark.parametrize(
    ("stdout", "stderr", "expected"),
    [
        ("stdout failure\n", "", "stdout failure"),
        ("", "stderr failure\n", "stderr failure"),
        ("", "", ""),
    ],
)
def test_vulture_process_failures_handle_empty_output_streams(
    stdout: str,
    stderr: str,
    expected: str,
) -> None:
    with pytest.raises(CheckOutputError) as raised:
        read_vulture(stdout=stdout, exit_code=2, stderr=stderr)

    assert raised.value.problem == expected


def test_vulture_exit_three_without_findings_is_an_error() -> None:
    with pytest.raises(CheckOutputError) as raised:
        read_vulture(stdout="\n", exit_code=3, stderr="Unexpected failure\n")

    assert raised.value.check_id == "dead-code"
    assert "vulture exited 3 without reporting findings" in str(raised.value)
    assert "Unexpected failure" in str(raised.value)


def test_vulture_exit_zero_with_findings_is_an_error() -> None:
    with pytest.raises(CheckOutputError) as raised:
        read_vulture(stdout=VULTURE_REPORT, exit_code=0, stderr="")

    assert raised.value.check_id == "dead-code"
    assert "vulture exited 0 but reported findings" in str(raised.value)
    assert VULTURE_REPORT.strip() in str(raised.value)


def test_vulture_runs_the_configured_arguments_under_a_custom_check_id(
    processes: FakeProcesses,
    command_factory: CommandFactory,
    tmp_path: Path,
) -> None:
    config_file = tmp_path / ".checksmith" / "vulture.toml"
    config_file.parent.mkdir()
    config_file.write_text("[tool.vulture]\n", encoding="utf-8")
    check = Check(
        id="custom-dead-code",
        package_type=PackageType.UVX,
        package="vulture==2.16",
        command=CommandName.VULTURE,
        args=(
            "--config",
            ConfigPath.model_validate(
                {"config_path": "vulture.toml"},
                context=tmp_path / ".checksmith" / "checksmith.yaml",
            ),
            "--min-confidence",
            "80",
            "src",
        ),
    )

    result = command_factory.for_name(name=CommandName.VULTURE).run(
        check=check, project_root=tmp_path, output=None
    )

    assert processes.started[0].argv == (
        "uvx",
        "--from",
        "vulture==2.16",
        "vulture",
        "--config",
        str(config_file),
        "--min-confidence",
        "80",
        "src",
    )
    assert processes.started[0].cwd == tmp_path
    assert result == CheckResult(
        check_id="custom-dead-code", status=CheckStatus.PASSED, messages=()
    )


def test_a_relative_vulture_config_is_found_from_the_project_root(
    processes: FakeProcesses,
    command_factory: CommandFactory,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "vulture.toml").write_text("[tool.vulture]\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    result = command_factory.for_name(name=CommandName.VULTURE).run(
        check=vulture_check(args=("--config", "vulture.toml", ".")),
        project_root=project,
        output=None,
    )

    assert result.status is CheckStatus.PASSED
    assert len(processes.started) == 1


@pytest.mark.parametrize(
    "args",
    [
        ("--config", "missing.toml", "."),
        ("--config=missing.toml", "."),
        ("--conf", "missing.toml", "."),
        ("--c=missing.toml", "."),
        ("--config", "vulture.toml", "--config", "missing.toml", "."),
        ("--config", "missing.toml", "--config=vulture.toml", "."),
    ],
)
def test_a_missing_vulture_config_is_an_error_before_execution(
    args: tuple[str, ...],
    processes: FakeProcesses,
    command_factory: CommandFactory,
    tmp_path: Path,
) -> None:
    (tmp_path / "vulture.toml").write_text("[tool.vulture]\n", encoding="utf-8")

    with pytest.raises(CheckPrerequisiteError) as raised:
        command_factory.for_name(name=CommandName.VULTURE).run(
            check=vulture_check(args=args), project_root=tmp_path, output=None
        )

    assert raised.value.check_id == "dead-code"
    assert raised.value.command is CommandName.VULTURE
    assert raised.value.config_file == tmp_path / "missing.toml"
    assert "silently runs with its defaults" in str(raised.value)
    assert isinstance(raised.value.__cause__, FileNotFoundError)
    assert processes.started == []


@pytest.mark.parametrize(
    ("argument", "config_file"),
    [("--config=directory", "directory"), ("--config=", "")],
)
def test_a_vulture_config_that_is_not_a_regular_file_is_an_error(
    argument: str,
    config_file: str,
    processes: FakeProcesses,
    command_factory: CommandFactory,
    tmp_path: Path,
) -> None:
    (tmp_path / "directory").mkdir()

    with pytest.raises(
        CheckPrerequisiteError, match="Expected a regular file"
    ) as raised:
        command_factory.for_name(name=CommandName.VULTURE).run(
            check=vulture_check(args=(argument, ".")),
            project_root=tmp_path,
            output=None,
        )

    assert raised.value.config_file == tmp_path / config_file
    assert processes.started == []


def test_a_dangling_vulture_config_symlink_is_an_error(
    processes: FakeProcesses, command_factory: CommandFactory, tmp_path: Path
) -> None:
    (tmp_path / "vulture.toml").symlink_to(tmp_path / "missing.toml")

    with pytest.raises(CheckPrerequisiteError):
        command_factory.for_name(name=CommandName.VULTURE).run(
            check=vulture_check(args=("--config", "vulture.toml", ".")),
            project_root=tmp_path,
            output=None,
        )

    assert processes.started == []


def test_an_uninspectable_vulture_config_is_an_error(
    processes: FakeProcesses, tmp_path: Path
) -> None:
    class UninspectableFiles(LocalProjectFiles):
        def stat(self, *, path: Path, follow_symlinks: bool) -> os.stat_result:
            raise PermissionError("Cannot inspect vulture.toml")

    command = VultureCommand(
        executor=processes,
        uv_prerequisites=UvProjectPrerequisites(
            environment={}, files=LocalProjectFiles()
        ),
        files=UninspectableFiles(),
    )

    with pytest.raises(CheckPrerequisiteError, match="Cannot inspect vulture.toml"):
        command.run(
            check=vulture_check(args=("--config", "vulture.toml", ".")),
            project_root=tmp_path,
            output=None,
        )

    assert processes.started == []


@pytest.mark.parametrize(
    "args",
    [
        (".",),
        ("--min-confidence", "80", "."),
        ("--exclude=--config", "."),
        ("--configuration=missing.toml", "."),
        ("--", "--config", "missing.toml"),
        (".", "--", "--config=missing.toml"),
    ],
)
def test_only_vulture_config_options_name_a_config_to_check(
    args: tuple[str, ...],
    processes: FakeProcesses,
    command_factory: CommandFactory,
    tmp_path: Path,
) -> None:
    result = command_factory.for_name(name=CommandName.VULTURE).run(
        check=vulture_check(args=args), project_root=tmp_path, output=None
    )

    assert result.status is CheckStatus.PASSED
    assert processes.started[0].argv[4:] == args


def test_a_config_option_without_a_path_is_left_for_vulture_to_reject(
    processes: FakeProcesses, command_factory: CommandFactory, tmp_path: Path
) -> None:
    processes.exit_code = 2
    processes.stderr = (
        "usage: vulture [options] [PATH ...]\n"
        "vulture: error: argument --config: expected one argument\n"
    )

    with pytest.raises(CheckOutputError, match="expected one argument"):
        command_factory.for_name(name=CommandName.VULTURE).run(
            check=vulture_check(args=(".", "--config")),
            project_root=tmp_path,
            output=None,
        )

    assert len(processes.started) == 1
