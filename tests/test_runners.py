import pytest

from checksmith.dtos import RunnerName
from checksmith.runners import NpxRunner, UvxRunner


@pytest.fixture
def npx_runner() -> NpxRunner:
    return NpxRunner()


def test_the_npx_runner_answers_to_its_config_name(npx_runner: NpxRunner) -> None:
    assert npx_runner.name is RunnerName.NPX


@pytest.mark.parametrize(
    "package",
    [
        "prettier@3.6.2",
        "prettier@^3.6.2",
        "prettier@~1.2",
        "prettier@>=1.0 <2",
        "prettier@1.x",
        "prettier@1.2.3 || >=2.0.0",
        "prettier@3.6.2-rc.1",
        "prettier@1.0.0+build.5",
        "@scope/name@1.0.0",
        "@scope/sub.name@^0.0.1",
    ],
)
def test_an_npm_name_at_a_constrained_range_is_accepted(
    package: str,
    npx_runner: NpxRunner,
) -> None:
    npx_runner.validate_package(package=package)


@pytest.mark.parametrize(
    "package",
    [
        "prettier",
        "@scope/name",
        "prettier@latest",
        "prettier@*",
        "prettier@x",
        "prettier@",
        "prettier@not a range",
        "github:user/repo",
        "file:../prettier",
        "https://example.com/p.tgz",
        "",
        "ruff==0.16.7",
    ],
)
def test_anything_that_is_not_a_constrained_npm_range_is_rejected(
    package: str,
    npx_runner: NpxRunner,
) -> None:
    with pytest.raises(ValueError):
        npx_runner.validate_package(package=package)


def test_an_npx_range_is_accepted_where_only_a_pin_once_was(
    npx_runner: NpxRunner,
) -> None:
    """``npx --package`` resolves a range, so Checksmith has no reason to refuse one."""
    npx_runner.validate_package(package="prettier@^3.6.2")


def test_the_name_half_is_left_for_npm_to_judge(npx_runner: NpxRunner) -> None:
    """npm owns the name grammar; restating it here would only age against it."""
    npx_runner.validate_package(package="not a name@1.0.0")


def test_a_scoped_name_keeps_its_scope_marker_out_of_the_range(
    npx_runner: NpxRunner,
) -> None:
    """The last ``@`` separates the range, so a scope marker is never mistaken
    for it.
    """
    npx_runner.validate_package(package="@scope/name@1.0.0")

    with pytest.raises(ValueError):
        npx_runner.validate_package(package="@scope/name")


def test_an_npm_tag_is_rejected_as_a_range(npx_runner: NpxRunner) -> None:
    with pytest.raises(ValueError, match="not a valid npm range"):
        npx_runner.validate_package(package="prettier@latest")


@pytest.mark.parametrize("package", ["prettier@*", "prettier@", "prettier@x"])
def test_an_npx_range_matching_every_version_is_rejected(
    package: str,
    npx_runner: NpxRunner,
) -> None:
    """``*``, ``x`` and an empty range all normalise to ``*``, constraining nothing."""
    with pytest.raises(ValueError, match="must constrain a version"):
        npx_runner.validate_package(package=package)


def test_an_npx_vector_pins_the_package_explicitly(npx_runner: NpxRunner) -> None:
    """``--package`` keeps npx from resolving the command name itself."""
    argv = npx_runner.build_argv(
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
    npx_runner: NpxRunner,
) -> None:
    """``^3.6.2`` reaches npx as written, not as ``>=3.6.2 <4.0.0``."""
    argv = npx_runner.build_argv(
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


@pytest.fixture
def uvx_runner() -> UvxRunner:
    return UvxRunner()


def test_the_uvx_runner_answers_to_its_config_name(uvx_runner: UvxRunner) -> None:
    assert uvx_runner.name is RunnerName.UVX


@pytest.mark.parametrize(
    "package",
    [
        "ruff==0.16.7",
        "ruff>=0.14,<0.15",
        "mypy[faster-cache]==1.18.0",
        "ruff~=1.0",
        "ruff!=1.0",
        "ruff==1.*",
        "ruff===1.0",
        "Some.Pkg==1.0",
        "some_pkg==1.0",
        "ruff == 1.0",
        "ruff==1!2.0",
        "ruff==1.0+local",
        "ruff==3.6.2rc1",
        'ruff==1.0; python_version < "3.9"',
        "a==1",
    ],
)
def test_a_python_requirement_with_a_version_is_accepted(
    package: str,
    uvx_runner: UvxRunner,
) -> None:
    uvx_runner.validate_package(package=package)


@pytest.mark.parametrize(
    "package",
    [
        "ruff",
        "ruff==",
        "==1.0",
        "",
        "./local-ruff",
        "git+https://example.com/ruff.git",
        "ruff @ git+https://example.com/ruff.git",
        "@scope/name@1.0.0",
        "prettier@3.6.2",
    ],
)
def test_anything_that_is_not_a_versioned_requirement_is_rejected(
    package: str,
    uvx_runner: UvxRunner,
) -> None:
    with pytest.raises(ValueError):
        uvx_runner.validate_package(package=package)


def test_a_uvx_range_is_accepted_where_only_a_pin_once_was(
    uvx_runner: UvxRunner,
) -> None:
    """``uvx --from`` resolves a range, so Checksmith has no reason to refuse one."""
    uvx_runner.validate_package(package="ruff>=0.14,<0.15")


def test_extras_travel_with_the_requirement(uvx_runner: UvxRunner) -> None:
    """``packaging`` owns the grammar, so extras need no handling of their own."""
    uvx_runner.validate_package(package="mypy[faster-cache]==1.18.0")


def test_a_requirement_naming_no_version_is_rejected(uvx_runner: UvxRunner) -> None:
    """A bare name resolves to whatever is newest today, which is not a gate."""
    with pytest.raises(ValueError, match="must constrain a version"):
        uvx_runner.validate_package(package="ruff")


def test_a_url_requirement_is_rejected_because_it_names_no_version(
    uvx_runner: UvxRunner,
) -> None:
    """PEP 508 forbids a URL and a specifier together, so the version rule
    catches it.
    """
    with pytest.raises(ValueError, match="must constrain a version"):
        uvx_runner.validate_package(package="ruff @ git+https://example.com/ruff.git")


def test_an_npm_specification_is_not_a_python_requirement(
    uvx_runner: UvxRunner,
) -> None:
    """``packaging`` reads this as the package ``prettier`` at the URL ``3.6.2``."""
    with pytest.raises(ValueError, match="must constrain a version"):
        uvx_runner.validate_package(package="prettier@3.6.2")


def test_a_requirement_that_does_not_parse_is_rejected(uvx_runner: UvxRunner) -> None:
    with pytest.raises(ValueError, match="not a valid Python requirement"):
        uvx_runner.validate_package(package="./local-ruff")


def test_a_uvx_vector_names_the_package_before_the_command(
    uvx_runner: UvxRunner,
) -> None:
    argv = uvx_runner.build_argv(
        package="ruff==0.16.7",
        command="ruff",
        arguments=("check", "."),
    )

    assert argv == ("uvx", "--from", "ruff==0.16.7", "ruff", "check", ".")


def test_a_uvx_vector_passes_a_uvx_range_through_unchanged(
    uvx_runner: UvxRunner,
) -> None:
    """The command that runs is the one the config file names, character for character."""
    argv = uvx_runner.build_argv(
        package="ruff>=0.14,<0.15",
        command="ruff",
        arguments=("check", "."),
    )

    assert argv == ("uvx", "--from", "ruff>=0.14,<0.15", "ruff", "check", ".")
