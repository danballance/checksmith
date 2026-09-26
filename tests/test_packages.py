import pytest

from checksmith.dtos import PackageType
from checksmith.packages import NpxPackage, Package, UvPackage, UvxPackage


@pytest.mark.parametrize(
    ("name", "package_class"),
    [("uv", UvPackage), (PackageType.UV, UvPackage)],
)
def test_the_uv_project_mode_resolves_from_its_name(
    name: str | PackageType,
    package_class: type[Package],
) -> None:
    package = Package.from_name(name)

    assert isinstance(package, package_class)
    assert package.name is PackageType.UV


def test_uv_accepts_an_explicit_null_package() -> None:
    UvPackage().validate_package(package=None)


@pytest.mark.parametrize("package", ["pytest", "pytest==9.0.2", ""])
def test_uv_rejects_a_package_override(package: str) -> None:
    with pytest.raises(ValueError, match="uv package must be null"):
        UvPackage().validate_package(package=package)


def test_uv_runs_the_project_command_with_a_locked_environment() -> None:
    argv = UvPackage().build_argv(
        package=None,
        command="pytest",
        arguments=("tests", "--maxfail=1"),
    )

    assert argv == ("uv", "run", "--locked", "pytest", "tests", "--maxfail=1")


def test_uv_refuses_a_package_override_when_building_the_vector() -> None:
    with pytest.raises(ValueError, match="uv package must be null"):
        UvPackage().build_argv(
            package="pytest==9.0.2",
            command="pytest",
            arguments=("tests",),
        )


@pytest.mark.parametrize("package", [NpxPackage(), UvxPackage()])
def test_isolated_packages_require_a_package_name(package: Package) -> None:
    with pytest.raises(ValueError, match="must name a versioned package"):
        package.validate_package(package=None)

    with pytest.raises(ValueError, match="must name a versioned package"):
        package.build_argv(package=None, command="tool", arguments=())


@pytest.fixture
def npx_package() -> NpxPackage:
    return NpxPackage()


def test_the_npx_package_answers_to_its_config_name(npx_package: NpxPackage) -> None:
    assert npx_package.name is PackageType.NPX


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
    npx_package: NpxPackage,
) -> None:
    npx_package.validate_package(package=package)


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
    npx_package: NpxPackage,
) -> None:
    with pytest.raises(ValueError):
        npx_package.validate_package(package=package)


def test_an_npx_range_is_accepted_where_only_a_pin_once_was(
    npx_package: NpxPackage,
) -> None:
    """``npx --package`` resolves a range, so Checksmith has no reason to refuse one."""
    npx_package.validate_package(package="prettier@^3.6.2")


def test_the_name_half_is_left_for_npm_to_judge(npx_package: NpxPackage) -> None:
    """npm owns the name grammar; restating it here would only age against it."""
    npx_package.validate_package(package="not a name@1.0.0")


def test_a_scoped_name_keeps_its_scope_marker_out_of_the_range(
    npx_package: NpxPackage,
) -> None:
    """The last ``@`` separates the range, so a scope marker is never mistaken
    for it.
    """
    npx_package.validate_package(package="@scope/name@1.0.0")

    with pytest.raises(ValueError):
        npx_package.validate_package(package="@scope/name")


def test_an_npm_tag_is_rejected_as_a_range(npx_package: NpxPackage) -> None:
    with pytest.raises(ValueError, match="not a valid npm range"):
        npx_package.validate_package(package="prettier@latest")


@pytest.mark.parametrize("package", ["prettier@*", "prettier@", "prettier@x"])
def test_an_npx_range_matching_every_version_is_rejected(
    package: str,
    npx_package: NpxPackage,
) -> None:
    """``*``, ``x`` and an empty range all normalise to ``*``, constraining nothing."""
    with pytest.raises(ValueError, match="must constrain a version"):
        npx_package.validate_package(package=package)


def test_an_npx_vector_pins_the_package_explicitly(npx_package: NpxPackage) -> None:
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
    npx_package: NpxPackage,
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


@pytest.fixture
def uvx_package() -> UvxPackage:
    return UvxPackage()


def test_the_uvx_package_answers_to_its_config_name(uvx_package: UvxPackage) -> None:
    assert uvx_package.name is PackageType.UVX


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
    uvx_package: UvxPackage,
) -> None:
    uvx_package.validate_package(package=package)


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
    uvx_package: UvxPackage,
) -> None:
    with pytest.raises(ValueError):
        uvx_package.validate_package(package=package)


def test_a_uvx_range_is_accepted_where_only_a_pin_once_was(
    uvx_package: UvxPackage,
) -> None:
    """``uvx --from`` resolves a range, so Checksmith has no reason to refuse one."""
    uvx_package.validate_package(package="ruff>=0.14,<0.15")


def test_extras_travel_with_the_requirement(uvx_package: UvxPackage) -> None:
    """``packaging`` owns the grammar, so extras need no handling of their own."""
    uvx_package.validate_package(package="mypy[faster-cache]==1.18.0")


def test_a_requirement_naming_no_version_is_rejected(uvx_package: UvxPackage) -> None:
    """A bare name resolves to whatever is newest today, which is not a gate."""
    with pytest.raises(ValueError, match="must constrain a version"):
        uvx_package.validate_package(package="ruff")


def test_an_unpinned_git_requirement_is_rejected(
    uvx_package: UvxPackage,
) -> None:
    with pytest.raises(ValueError, match="full 40-character hexadecimal commit"):
        uvx_package.validate_package(package="ruff @ git+https://example.com/ruff.git")


def test_an_npm_specification_is_not_a_python_requirement(
    uvx_package: UvxPackage,
) -> None:
    """``packaging`` reads this as the package ``prettier`` at the URL ``3.6.2``."""
    with pytest.raises(ValueError, match="must use git\\+https"):
        uvx_package.validate_package(package="prettier@3.6.2")


GIT_COMMIT = "3aa103334500ba2f6e31e01c99e650a8efa41de7"
GIT_REPOSITORY = "git+https://github.com/danballance/pyarchgraph"


@pytest.mark.parametrize(
    "package",
    [
        f"pyarchgraph @ {GIT_REPOSITORY}@{GIT_COMMIT}",
        f"pyarchgraph @ {GIT_REPOSITORY}.git@{GIT_COMMIT.upper()}",
        f'pyarchgraph[extra] @ {GIT_REPOSITORY}@{GIT_COMMIT} ; python_version >= "3.14"',
        f"pyarchgraph @ git+https://example.com:8443/project.git@{GIT_COMMIT}",
    ],
)
def test_a_named_git_requirement_pinned_to_a_full_commit_is_accepted(
    package: str,
    uvx_package: UvxPackage,
) -> None:
    uvx_package.validate_package(package=package)


@pytest.mark.parametrize(
    "url",
    [
        GIT_REPOSITORY,
        f"{GIT_REPOSITORY}@main",
        f"{GIT_REPOSITORY}@v0.1.0",
        f"{GIT_REPOSITORY}@{GIT_COMMIT[:7]}",
        f"{GIT_REPOSITORY}@{'g' * 40}",
        f"{GIT_REPOSITORY}@{GIT_COMMIT}0",
        f"{GIT_REPOSITORY}@{GIT_COMMIT}?download=1",
        f"{GIT_REPOSITORY}@{GIT_COMMIT}?",
        f"{GIT_REPOSITORY}@{GIT_COMMIT}#subdirectory=src",
        f"{GIT_REPOSITORY}@{GIT_COMMIT}#",
        f"https://example.com/project.git@{GIT_COMMIT}",
        f"git+http://example.com/project.git@{GIT_COMMIT}",
        f"git+ssh://example.com/project.git@{GIT_COMMIT}",
        f"git+https:///project.git@{GIT_COMMIT}",
        f"git+https://example.com/@{GIT_COMMIT}",
        f"git+https://example.com@{GIT_COMMIT}",
        f"git+https://[invalid/project.git@{GIT_COMMIT}",
        f"git+https://example.com:invalid/project.git@{GIT_COMMIT}",
        f"git+https://example.com:65536/project.git@{GIT_COMMIT}",
        f"git+https://example.com:0/project.git@{GIT_COMMIT}",
        f"git+https://example.com\\repository/project.git@{GIT_COMMIT}",
    ],
)
def test_git_sources_outside_the_pinned_https_scope_are_rejected(
    url: str,
    uvx_package: UvxPackage,
) -> None:
    with pytest.raises(ValueError, match="full 40-character hexadecimal commit"):
        uvx_package.validate_package(package=f"pyarchgraph @ {url}")


def test_a_git_requirement_reaches_uvx_unchanged(uvx_package: UvxPackage) -> None:
    package = f"pyarchgraph @ {GIT_REPOSITORY}@{GIT_COMMIT}"

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


def test_a_requirement_that_does_not_parse_is_rejected(uvx_package: UvxPackage) -> None:
    with pytest.raises(ValueError, match="not a valid Python requirement"):
        uvx_package.validate_package(package="./local-ruff")


def test_a_uvx_vector_names_the_package_before_the_command(
    uvx_package: UvxPackage,
) -> None:
    argv = uvx_package.build_argv(
        package="ruff==0.16.7",
        command="ruff",
        arguments=("check", "."),
    )

    assert argv == ("uvx", "--from", "ruff==0.16.7", "ruff", "check", ".")


def test_a_uvx_vector_passes_a_uvx_range_through_unchanged(
    uvx_package: UvxPackage,
) -> None:
    """The command that runs is the one the config file names, character for character."""
    argv = uvx_package.build_argv(
        package="ruff>=0.14,<0.15",
        command="ruff",
        arguments=("check", "."),
    )

    assert argv == ("uvx", "--from", "ruff>=0.14,<0.15", "ruff", "check", ".")
