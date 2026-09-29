from typing import Any

from checksmith.adapters.driven.execution.packages import build_check_argv
from checksmith.domain.config import Check
from checksmith.domain.models import CommandName, PackageType
from tests.checksmith.domain.test_config import check, document, parse

# The argument vector


def resolved(**overrides: Any) -> Check:
    """One check, built the only way a check can be built."""
    return parse(document(checks=[check(**overrides)])).checks[0]


def test_a_uvx_check_builds_the_documented_vector() -> None:
    assert build_check_argv(resolved(args=["check", "--config", "./ruff.toml", "."])) == (
        "uvx",
        "--from",
        "ruff==0.16.7",
        "ruff",
        "check",
        "--config",
        "./ruff.toml",
        ".",
    )


def test_a_pytest_check_builds_a_locked_project_vector() -> None:
    configured = resolved(
        id="tests",
        package_type="uv",
        package=None,
        command="pytest",
        args=["tests"],
    )

    assert configured.command is CommandName.PYTEST
    assert configured.package_type is PackageType.UV
    assert configured.package is None
    assert build_check_argv(configured) == ("uv", "run", "--locked", "pytest", "tests")


def test_other_commands_can_use_the_uv_project_environment() -> None:
    configured = resolved(package_type="uv", package=None, args=["check", "."])

    assert build_check_argv(configured) == ("uv", "run", "--locked", "ruff", "check", ".")


def test_a_semgrep_check_is_accepted_and_builds_a_uvx_vector() -> None:
    configured = resolved(
        id="function-style",
        package="semgrep==1.176.1",
        command="semgrep",
        args=["scan", "--json", "--config", ".checksmith/semgrep.yaml", "."],
    )

    assert configured.command is CommandName.SEMGREP
    assert build_check_argv(configured) == (
        "uvx",
        "--from",
        "semgrep==1.176.1",
        "semgrep",
        "scan",
        "--json",
        "--config",
        ".checksmith/semgrep.yaml",
        ".",
    )


def test_a_complexipy_check_is_accepted_and_builds_a_uvx_vector() -> None:
    configured = resolved(
        id="cognitive-complexity",
        package="complexipy==8.0.1",
        command="complexipy",
        args=["--plain", "--failed", "--max-complexity-allowed", "10", "."],
    )

    assert configured.id == "cognitive-complexity"
    assert configured.command is CommandName.COMPLEXIPY
    assert build_check_argv(configured) == (
        "uvx",
        "--from",
        "complexipy==8.0.1",
        "complexipy",
        "--plain",
        "--failed",
        "--max-complexity-allowed",
        "10",
        ".",
    )


def test_a_ty_check_is_accepted_and_builds_a_uvx_vector() -> None:
    configured = resolved(
        id="type-check",
        package="ty==0.0.80",
        command="ty",
        args=["check", "--output-format", "gitlab", "--error-on-warning", "."],
    )

    assert configured.id == "type-check"
    assert configured.command is CommandName.TY
    assert build_check_argv(configured) == (
        "uvx",
        "--from",
        "ty==0.0.80",
        "ty",
        "check",
        "--output-format",
        "gitlab",
        "--error-on-warning",
        ".",
    )


def test_pyarchgraph_configuration_passes_roots_and_gate_unchanged() -> None:
    configured = resolved(
        command="pyarchgraph",
        package="pyarchgraph @ git+https://github.com/danballance/pyarchgraph@main",
        args=["src", "plugins", "--exclude", "vendor/*", "--gate", "module-body"],
    )
    assert configured.command is CommandName.PYARCHGRAPH
    assert build_check_argv(configured) == (
        "uvx",
        "--isolated",
        "--refresh-package",
        "pyarchgraph",
        "--from",
        "pyarchgraph @ git+https://github.com/danballance/pyarchgraph@main",
        "pyarchgraph",
        "src",
        "plugins",
        "--exclude",
        "vendor/*",
        "--gate",
        "module-body",
    )


def test_pyarchgraph_can_run_from_its_pinned_git_source() -> None:
    package = (
        "pyarchgraph @ git+https://github.com/danballance/pyarchgraph"
        "@3aa103334500ba2f6e31e01c99e650a8efa41de7"
    )
    configured = resolved(
        command="pyarchgraph",
        package=package,
        args=["src"],
    )

    assert build_check_argv(configured)[:4] == ("uvx", "--from", package, "pyarchgraph")


def test_a_git_branch_configuration_requests_refresh_and_isolation() -> None:
    package = "analysis-tools @ git+https://example.com/tools.git@feature/parser"
    configured = resolved(
        command="pyarchgraph",
        package=package,
        args=["src", "--exclude", "vendor/*"],
    )

    assert build_check_argv(configured) == (
        "uvx",
        "--isolated",
        "--refresh-package",
        "analysis-tools",
        "--from",
        package,
        "pyarchgraph",
        "src",
        "--exclude",
        "vendor/*",
    )


def test_a_check_with_no_arguments_still_builds_a_runnable_vector() -> None:
    assert build_check_argv(resolved(args=[])) == ("uvx", "--from", "ruff==0.16.7", "ruff")


def test_an_argument_containing_spaces_stays_one_element() -> None:
    """The vector is never joined into a string, so nothing needs quoting."""
    vector = build_check_argv(resolved(args=["--config", "/my project/ruff.toml"]))

    assert vector[-1] == "/my project/ruff.toml"


def test_the_vector_is_derived_rather_than_declared() -> None:
    """Execution details do not become configuration fields."""
    assert "argv" not in Check.model_fields
    assert "argv" not in resolved().model_dump()
