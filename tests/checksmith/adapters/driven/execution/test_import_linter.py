import logging
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Final

import pytest

from checksmith.adapters.driven.execution.import_linter import (
    ImportLinterCommand,
    ImportLinterPrerequisites,
)
from checksmith.adapters.driven.execution.prerequisites import (
    LocalProjectFiles,
    UvProjectPrerequisites,
)
from checksmith.adapters.driven.execution.registry import CommandFactory
from checksmith.domain.config import Argument, Check, ConfigPath
from checksmith.domain.errors import CheckOutputError, CheckPrerequisiteError
from checksmith.domain.models import CheckResult, CheckStatus, CommandName, PackageType
from tests.conftest import FakeProcesses, make_command_factory

PROJECT_ROOT: Final = Path("/workspace/project")
CONTRACTS: Final = """\
[tool.importlinter]
root_package = "example"

[[tool.importlinter.contracts]]
name = "Keep the layers separate"
type = "layers"
layers = ["example.application", "example.domain"]
"""
BROKEN_REPORT: Final = """\
Contracts
---------

Analyzed 3 files, 2 dependencies.
Keep the layers separate BROKEN

Contracts: 0 kept, 1 broken.

Broken contracts
----------------
example.domain is not allowed to import example.application:
- example.domain -> example.application (l. 1)
"""


def import_linter_check(*, args: tuple[Argument, ...]) -> Check:
    return Check(
        id="dependencies",
        package_type=PackageType.UVX,
        package="import-linter==2.10",
        command=CommandName.IMPORT_LINTER,
        args=args,
    )


def read_import_linter(*, stdout: str, stderr: str, exit_code: int) -> CheckResult:
    return (
        make_command_factory(executor=FakeProcesses())
        .for_name(name=CommandName.IMPORT_LINTER)
        .process_response(
            check_id="dependencies",
            project_root=PROJECT_ROOT,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
        )
    )


def test_importing_import_linter_does_not_load_other_command_implementations(
    imported_modules: Callable[[str], frozenset[str]],
) -> None:
    modules = imported_modules("checksmith.adapters.driven.execution.import_linter")

    assert not modules & {
        "checksmith.adapters.driven.execution.registry",
        "checksmith.adapters.driven.execution.ruff",
        "checksmith.adapters.driven.execution.semgrep",
        "checksmith.adapters.driven.execution.ty",
        "importlinter",
    }


@pytest.mark.parametrize(
    "config",
    [
        None,
        "",
        '[project]\nname = "example"',
        "[tool]",
        "[tool.ruff]",
        "[tool.importlinter]",
        "[tool.importlinter]\ncontracts = []",
    ],
)
def test_absent_or_empty_contracts_make_import_linter_ineligible(
    command_factory: CommandFactory,
    config: str | None,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    if config is not None:
        (tmp_path / "pyproject.toml").write_text(config, encoding="utf-8")
    caplog.set_level(logging.DEBUG, logger="checksmith.adapters.driven.execution.import_linter")

    runnable = command_factory.for_name(
        name=CommandName.IMPORT_LINTER
    ).check_is_runnable(
        check=import_linter_check(args=("lint", "--config", "pyproject.toml")),
        project_root=tmp_path,
    )

    assert runnable is False
    assert "dependencies is not runnable" in caplog.text


@pytest.mark.parametrize("config", [CONTRACTS, "[[tool.importlinter.contracts]]"])
def test_contract_tables_make_import_linter_eligible_without_validating_their_options(
    command_factory: CommandFactory,
    config: str,
    tmp_path: Path,
) -> None:
    (tmp_path / "pyproject.toml").write_text(config, encoding="utf-8")

    assert (
        command_factory.for_name(name=CommandName.IMPORT_LINTER).check_is_runnable(
            check=import_linter_check(args=("lint", "--config=pyproject.toml")),
            project_root=tmp_path,
        )
        is True
    )


@pytest.mark.parametrize(
    "config",
    [
        "tool = []",
        "[tool]\nimportlinter = 42",
        "[tool.importlinter]\ncontracts = 3",
        '[tool.importlinter]\ncontracts = "rules"',
        "[tool.importlinter.contracts]",
        '[tool.importlinter]\ncontracts = ["rules"]',
        '[tool.importlinter]\ncontracts = [{name = "first"}, 3]',
    ],
)
def test_malformed_configuration_shapes_are_prerequisite_errors(
    command_factory: CommandFactory,
    config: str,
    tmp_path: Path,
) -> None:
    path = tmp_path / "pyproject.toml"
    path.write_text(config, encoding="utf-8")

    with pytest.raises(CheckPrerequisiteError) as raised:
        command_factory.for_name(name=CommandName.IMPORT_LINTER).check_is_runnable(
            check=import_linter_check(args=("lint", "--config", "pyproject.toml")),
            project_root=tmp_path,
        )

    assert raised.value.check_id == "dependencies"
    assert raised.value.command is CommandName.IMPORT_LINTER
    assert raised.value.config_file == path
    assert "must be" in raised.value.problem


@pytest.mark.parametrize("content", [b"[tool.importlinter", b"\xff"])
def test_invalid_toml_or_encoding_is_a_prerequisite_error(
    command_factory: CommandFactory,
    content: bytes,
    tmp_path: Path,
) -> None:
    (tmp_path / "pyproject.toml").write_bytes(content)

    with pytest.raises(CheckPrerequisiteError) as raised:
        command_factory.for_name(name=CommandName.IMPORT_LINTER).check_is_runnable(
            check=import_linter_check(args=("lint", "--config", "pyproject.toml")),
            project_root=tmp_path,
        )

    assert raised.value.__cause__ is not None


@pytest.mark.parametrize("exists_as_file", [True, False])
def test_a_missing_or_non_directory_project_root_is_an_error(
    command_factory: CommandFactory,
    exists_as_file: bool,
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "missing-project"
    if exists_as_file:
        project_root.write_text("not a directory", encoding="utf-8")

    with pytest.raises(CheckPrerequisiteError):
        command_factory.for_name(name=CommandName.IMPORT_LINTER).check_is_runnable(
            check=import_linter_check(args=("lint", "--config", "pyproject.toml")),
            project_root=project_root,
        )


def test_a_pyproject_directory_is_an_error(
    command_factory: CommandFactory, tmp_path: Path
) -> None:
    (tmp_path / "pyproject.toml").mkdir()

    with pytest.raises(CheckPrerequisiteError, match="directory"):
        command_factory.for_name(name=CommandName.IMPORT_LINTER).check_is_runnable(
            check=import_linter_check(args=("lint", "--config", "pyproject.toml")),
            project_root=tmp_path,
        )


def test_a_dangling_pyproject_symlink_is_an_error(
    command_factory: CommandFactory, tmp_path: Path
) -> None:
    (tmp_path / "pyproject.toml").symlink_to(tmp_path / "missing.toml")

    with pytest.raises(CheckPrerequisiteError):
        command_factory.for_name(name=CommandName.IMPORT_LINTER).check_is_runnable(
            check=import_linter_check(args=("lint", "--config", "pyproject.toml")),
            project_root=tmp_path,
        )


def test_an_unreadable_pyproject_is_an_error(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(CONTRACTS, encoding="utf-8")

    class UnreadableFiles(LocalProjectFiles):
        def read_toml(self, *, path: Path) -> Mapping[str, object]:
            raise PermissionError("Cannot read pyproject.toml")

    prerequisites = ImportLinterPrerequisites(files=UnreadableFiles())

    with pytest.raises(CheckPrerequisiteError, match="Cannot read pyproject.toml"):
        prerequisites.is_runnable(
            check=import_linter_check(args=("lint", "--config", "pyproject.toml")),
            project_root=tmp_path,
        )


def test_only_the_project_root_is_inspected(
    command_factory: CommandFactory,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "pyproject.toml").write_text(CONTRACTS, encoding="utf-8")
    project_root = tmp_path / "child"
    project_root.mkdir()
    (project_root / ".importlinter").write_text("[importlinter]", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    assert (
        command_factory.for_name(name=CommandName.IMPORT_LINTER).check_is_runnable(
            check=import_linter_check(args=("lint", "--config", "pyproject.toml")),
            project_root=project_root,
        )
        is False
    )


@pytest.mark.parametrize(
    "args",
    [
        (),
        ("explore",),
        ("lint",),
        ("lint", "--config"),
        ("lint", "--config", "--no-logo"),
        ("lint", "--config", ""),
        ("lint", "--config="),
        ("lint", "--config", "other.toml"),
        ("lint", "--config", "../pyproject.toml"),
        ("lint", "--", "--config", "pyproject.toml"),
        ("lint", "--contract", "--config=pyproject.toml"),
        ("lint", "--cache-dir", "--config", "pyproject.toml"),
        ("lint", "--config", "pyproject.toml", "--config=pyproject.toml"),
    ],
)
def test_invalid_arguments_are_errors_even_without_a_pyproject(
    command_factory: CommandFactory,
    args: tuple[str, ...],
    tmp_path: Path,
) -> None:
    with pytest.raises(CheckPrerequisiteError) as raised:
        command_factory.for_name(name=CommandName.IMPORT_LINTER).check_is_runnable(
            check=import_linter_check(args=args),
            project_root=tmp_path,
        )

    assert raised.value.check_id == "dependencies"


@pytest.mark.parametrize("option", ["--contract", "--cache-dir"])
def test_option_values_are_not_mistaken_for_additional_config_arguments(
    command_factory: CommandFactory,
    option: str,
    tmp_path: Path,
) -> None:
    (tmp_path / "pyproject.toml").write_text(CONTRACTS, encoding="utf-8")

    assert (
        command_factory.for_name(name=CommandName.IMPORT_LINTER).check_is_runnable(
            check=import_linter_check(
                args=(
                    "lint",
                    "--config",
                    "pyproject.toml",
                    option,
                    "--config=other.toml",
                ),
            ),
            project_root=tmp_path,
        )
        is True
    )


def test_a_lexically_matching_path_cannot_follow_a_symlink_to_another_config(
    command_factory: CommandFactory,
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "pyproject.toml").write_text(CONTRACTS, encoding="utf-8")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "child").mkdir()
    (elsewhere / "pyproject.toml").write_text(CONTRACTS, encoding="utf-8")
    (project_root / "link").symlink_to(elsewhere / "child", target_is_directory=True)

    with pytest.raises(CheckPrerequisiteError, match="--config must target"):
        command_factory.for_name(name=CommandName.IMPORT_LINTER).check_is_runnable(
            check=import_linter_check(
                args=("lint", "--config", "link/../pyproject.toml")
            ),
            project_root=project_root,
        )


def test_an_unresolvable_config_path_is_a_prerequisite_error(tmp_path: Path) -> None:
    class UnresolvableFiles(LocalProjectFiles):
        def resolve(self, *, path: Path) -> Path:
            raise PermissionError("Cannot resolve pyproject.toml")

    prerequisites = ImportLinterPrerequisites(files=UnresolvableFiles())

    with pytest.raises(CheckPrerequisiteError, match="Cannot resolve pyproject.toml"):
        prerequisites.is_runnable(
            check=import_linter_check(args=("lint", "--config", "pyproject.toml")),
            project_root=tmp_path,
        )


@pytest.mark.parametrize("joined", [False, True])
@pytest.mark.parametrize("absolute", [False, True])
def test_configuration_paths_are_normalized_against_the_project_root(
    command_factory: CommandFactory,
    joined: bool,
    absolute: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = tmp_path / "project with spaces"
    project_root.mkdir()
    (project_root / "pyproject.toml").write_text(CONTRACTS, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    relative_path = "./unused/../pyproject.toml"
    path = str(project_root / relative_path) if absolute else relative_path
    args = ("lint", f"--config={path}") if joined else ("lint", "--config", path)

    assert (
        command_factory.for_name(name=CommandName.IMPORT_LINTER).check_is_runnable(
            check=import_linter_check(args=args),
            project_root=project_root,
        )
        is True
    )


def test_a_config_path_argument_is_supported(
    command_factory: CommandFactory, tmp_path: Path
) -> None:
    (tmp_path / "pyproject.toml").write_text(CONTRACTS, encoding="utf-8")
    config_path = ConfigPath.model_validate(
        {"config_path": "../pyproject.toml"},
        context=tmp_path / ".checksmith" / "checks.yaml",
    )

    assert (
        command_factory.for_name(name=CommandName.IMPORT_LINTER).check_is_runnable(
            check=import_linter_check(args=("lint", "--config", config_path)),
            project_root=tmp_path,
        )
        is True
    )


@pytest.mark.parametrize(
    "summary", ["Contracts: 2 kept, 0 broken.", "Contracts: 0 kept, 0 broken."]
)
def test_a_clean_report_passes_and_preserves_the_report(summary: str) -> None:
    assert read_import_linter(stdout=summary, stderr=" \n", exit_code=0) == CheckResult(
        check_id="dependencies",
        status=CheckStatus.PASSED,
        messages=(summary,),
    )


def test_a_broken_report_fails_and_preserves_violation_details() -> None:
    assert read_import_linter(
        stdout=BROKEN_REPORT, stderr="", exit_code=1
    ) == CheckResult(
        check_id="dependencies",
        status=CheckStatus.FAILED,
        messages=(BROKEN_REPORT.strip(),),
    )


def test_warnings_do_not_change_the_report_status() -> None:
    stdout = (
        "Contracts: 1 kept, 0 broken.\n\nWarnings\nAn ignored import was not found.\n"
    )
    stderr = "Dependency deprecation warning.\n"

    result = read_import_linter(stdout=stdout, stderr=stderr, exit_code=0)

    assert result.status is CheckStatus.PASSED
    assert result.messages == (stdout.strip(), stderr.strip())


@pytest.mark.parametrize(
    "stdout",
    [
        "",
        "Could not import example.",
        "Contracts: 1 kept, 0 broken",
        "Contracts: -1 kept, 0 broken.",
        "Contracts: 1 kept, -1 broken.",
        "prefix Contracts: 1 kept, 0 broken.",
        "Contracts: 1 kept, 0 broken. suffix",
        " Contracts: 1 kept, 0 broken.",
        "Contracts: 1 kept, 0 broken.\nContracts: 1 kept, 0 broken.",
    ],
)
def test_missing_malformed_or_duplicate_summaries_are_errors(stdout: str) -> None:
    with pytest.raises(CheckOutputError) as raised:
        read_import_linter(stdout=stdout, stderr="diagnostic from stderr", exit_code=0)

    assert raised.value.check_id == "dependencies"
    assert raised.value.command is CommandName.IMPORT_LINTER
    assert "exactly one complete" in raised.value.summary
    assert stdout.strip() in raised.value.problem
    assert "diagnostic from stderr" in raised.value.problem


def test_a_summary_on_stderr_is_not_a_report() -> None:
    with pytest.raises(CheckOutputError):
        read_import_linter(
            stdout="", stderr="Contracts: 1 kept, 0 broken.", exit_code=0
        )


@pytest.mark.parametrize(
    ("exit_code", "stdout"),
    [(0, "Contracts: 0 kept, 1 broken."), (1, "Contracts: 1 kept, 0 broken.")],
)
def test_exit_codes_must_agree_with_the_report(exit_code: int, stdout: str) -> None:
    with pytest.raises(CheckOutputError, match="contradicts its exit code"):
        read_import_linter(stdout=stdout, stderr="", exit_code=exit_code)


@pytest.mark.parametrize("exit_code", [2, 3, -9])
def test_process_failures_are_errors_even_with_a_report(exit_code: int) -> None:
    with pytest.raises(CheckOutputError) as raised:
        read_import_linter(
            stdout=BROKEN_REPORT, stderr="process failure", exit_code=exit_code
        )

    assert f"import-linter exited {exit_code}" in raised.value.summary
    assert BROKEN_REPORT.strip() in raised.value.problem
    assert "process failure" in raised.value.problem


def test_an_installation_failure_preserves_the_uvx_diagnostic() -> None:
    with pytest.raises(CheckOutputError, match="Failed to download import-linter"):
        read_import_linter(
            stdout="", stderr="Failed to download import-linter", exit_code=1
        )


def test_import_linter_runs_through_uvx_with_unchanged_arguments(
    command_factory: CommandFactory,
    processes: FakeProcesses,
) -> None:
    processes.stdout = "Contracts: 2 kept, 0 broken.\n"
    check = import_linter_check(
        args=("lint", "--config", "pyproject.toml", "--no-logo")
    )

    result = command_factory.for_name(name=CommandName.IMPORT_LINTER).run(
        check=check, project_root=PROJECT_ROOT, output=None
    )

    assert processes.started[0].argv == (
        "uvx",
        "--from",
        "import-linter==2.10",
        "import-linter",
        "lint",
        "--config",
        "pyproject.toml",
        "--no-logo",
    )
    assert processes.started[0].cwd == PROJECT_ROOT
    assert result.status is CheckStatus.PASSED


def test_command_delegates_eligibility_without_starting_a_process(
    processes: FakeProcesses,
) -> None:
    calls: list[tuple[Check, Path]] = []

    class Eligibility:
        def is_runnable(self, *, check: Check, project_root: Path) -> bool:
            calls.append((check, project_root))
            return False

    check = import_linter_check(args=("lint", "--config", "pyproject.toml"))
    command = ImportLinterCommand(
        executor=processes,
        uv_prerequisites=UvProjectPrerequisites(
            environment={}, files=LocalProjectFiles()
        ),
        prerequisites=Eligibility(),
    )

    assert command.check_is_runnable(check=check, project_root=PROJECT_ROOT) is False
    assert calls == [(check, PROJECT_ROOT)]
    assert processes.started == []
