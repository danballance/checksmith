import pytest

from checksmith.adapters.driven.execution.packages import (
    NpxInvocation,
    PackageInvocation,
    UvInvocation,
    UvxInvocation,
)
from checksmith.domain.models import PackageType

GIT_COMMIT = "3aa103334500ba2f6e31e01c99e650a8efa41de7"
GIT_REPOSITORY = "git+https://github.com/danballance/pyarchgraph"


@pytest.fixture
def npx_package() -> NpxInvocation:
    return NpxInvocation()


@pytest.fixture
def uvx_package() -> UvxInvocation:
    return UvxInvocation()


@pytest.mark.parametrize("invocation", [NpxInvocation(), UvxInvocation()])
def test_isolated_invocations_require_a_package_name(invocation: PackageInvocation) -> None:
    with pytest.raises(ValueError, match="must name a versioned package"):
        invocation.build_argv(package=None, command="tool", arguments=())


@pytest.mark.parametrize(
    ("name", "expected"),
    [(PackageType.NPX, NpxInvocation), (PackageType.UVX, UvxInvocation),
     (PackageType.UV, UvInvocation)],
)
def test_package_type_selects_invocation(
    name: PackageType, expected: type[PackageInvocation]
) -> None:
    assert isinstance(PackageInvocation.from_name(name), expected)


def test_uv_runs_the_project_command_with_a_locked_environment() -> None:
    argv = UvInvocation().build_argv(
        package=None,
        command="pytest",
        arguments=("tests", "--maxfail=1"),
    )

    assert argv == ("uv", "run", "--locked", "pytest", "tests", "--maxfail=1")



def test_uv_refuses_a_package_override_when_building_the_vector() -> None:
    with pytest.raises(ValueError, match="uv package must be null"):
        UvInvocation().build_argv(
            package="pytest==9.0.2",
            command="pytest",
            arguments=("tests",),
        )



def test_an_npx_vector_pins_the_package_explicitly(npx_package: NpxInvocation) -> None:
    """``--package`` keeps npx from resolving the command name itself."""
    argv = npx_package.build_argv(
        package="prettier@3.6.2",
        command="prettier",
        arguments=("--check", "."),
    )

    assert argv == (
        "npx",
        "--yes",
        "--package",
        "prettier@3.6.2",
        "prettier",
        "--check",
        ".",
    )



def test_an_npx_vector_passes_an_npx_range_through_unexpanded(
    npx_package: NpxInvocation,
) -> None:
    """``^3.6.2`` reaches npx as written, not as ``>=3.6.2 <4.0.0``."""
    argv = npx_package.build_argv(
        package="prettier@^3.6.2",
        command="prettier",
        arguments=("--check",),
    )

    assert argv == (
        "npx",
        "--yes",
        "--package",
        "prettier@^3.6.2",
        "prettier",
        "--check",
    )



@pytest.mark.parametrize("commit", [GIT_COMMIT, GIT_COMMIT.upper()])
def test_a_pinned_git_requirement_reaches_uvx_without_refresh(
    commit: str,
    uvx_package: UvxInvocation,
) -> None:
    package = f"pyarchgraph @ {GIT_REPOSITORY}@{commit}"

    argv = uvx_package.build_argv(
        package=package,
        command="pyarchgraph",
        arguments=("src", "--output", "json"),
    )

    assert argv == (
        "uvx",
        "--from",
        package,
        "pyarchgraph",
        "src",
        "--output",
        "json",
    )



@pytest.mark.parametrize(
    "ref",
    ["main", "feature/example", "v0.1.0", GIT_COMMIT[:7], "g" * 40, GIT_COMMIT + "0"],
)
def test_explicit_git_refs_are_refreshed_in_an_isolated_environment(
    ref: str,
    uvx_package: UvxInvocation,
) -> None:
    package = f"analysis-tools @ git+https://example.com/tools.git@{ref}"

    argv = uvx_package.build_argv(
        package=package,
        command="analyze",
        arguments=("src directory", "--output", "json"),
    )

    assert argv == (
        "uvx",
        "--isolated",
        "--refresh-package",
        "analysis-tools",
        "--from",
        package,
        "analyze",
        "src directory",
        "--output",
        "json",
    )



def test_git_refresh_preserves_extras_and_markers(uvx_package: UvxInvocation) -> None:
    package = (
        "Analysis.Tools[extra] @ git+https://example.com/tools.git@feature/example"
        ' ; python_version >= "3.14"'
    )


    assert uvx_package.build_argv(
        package=package,
        command="analyze",
        arguments=(),
    ) == (
        "uvx",
        "--isolated",
        "--refresh-package",
        "Analysis.Tools",
        "--from",
        package,
        "analyze",
    )



def test_a_uvx_vector_names_the_package_before_the_command(
    uvx_package: UvxInvocation,
) -> None:
    argv = uvx_package.build_argv(
        package="ruff==0.16.7",
        command="ruff",
        arguments=("check", "."),
    )

    assert argv == ("uvx", "--from", "ruff==0.16.7", "ruff", "check", ".")



def test_a_uvx_vector_passes_a_uvx_range_through_unchanged(
    uvx_package: UvxInvocation,
) -> None:
    """The command that runs is the one the config file names, character for character."""
    argv = uvx_package.build_argv(
        package="ruff>=0.14,<0.15",
        command="ruff",
        arguments=("check", "."),
    )

    assert argv == ("uvx", "--from", "ruff>=0.14,<0.15", "ruff", "check", ".")
