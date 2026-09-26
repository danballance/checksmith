"""Tests for :mod:`checksmith.config`."""

import hashlib
from collections.abc import Callable, Mapping
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from checksmith.config import Argument, Check, Config, ConfigPath
from checksmith.dtos import CommandName, PackageType
from checksmith.errors import ConfigSchemaError, ConfigSyntaxError

CONFIG_PATH = Path("/project/.checksmith/checksmith.yaml")


def check(**overrides: Any) -> dict[str, Any]:
    """A minimal valid check, with fields replaced or removed by keyword."""
    body: dict[str, Any] = {
        "id": "ruff",
        "package_type": "uvx",
        "package": "ruff==0.16.7",
        "command": "ruff",
        "args": ["check"],
    }
    body.update(overrides)
    return {name: value for name, value in body.items() if value is not ...}


def document(**overrides: Any) -> dict[object, Any]:
    """A minimal valid config file, with fields replaced or removed by keyword.

    Keyed by ``object`` so a config file with a key that is not a string reaches
    the schema, which names it, rather than failing an annotation on the way in.
    """
    body: dict[object, Any] = {"schema_version": 1, "checks": [check()]}
    body.update(overrides)
    return {name: value for name, value in body.items() if value is not ...}


def parse(body: Mapping[object, Any]) -> Config:
    return Config.from_mapping(document=body, config_file=CONFIG_PATH)


def reject(body: Mapping[object, Any]) -> ConfigSchemaError:
    """Parse a config file that must be rejected, and return the error."""
    with pytest.raises(ConfigSchemaError) as raised:
        parse(body)
    return raised.value


def fields_of(error: ConfigSchemaError) -> tuple[str, ...]:
    return tuple(violation.field for violation in error.violations)


@pytest.fixture
def load(tmp_path: Path) -> Callable[[str], Config]:
    """Write a config file and load it through the real entry point."""

    def _load(text: str) -> Config:
        path = tmp_path / "checksmith.yaml"
        path.write_text(text, encoding="utf-8")
        return Config.from_path(config_file=path, working_directory=tmp_path)

    return _load


@pytest.fixture
def refuse(load: Callable[[str], Config]) -> Callable[[str], ConfigSyntaxError]:
    """Load a config file that must be refused before parsing, and return the error."""

    def _refuse(text: str) -> ConfigSyntaxError:
        with pytest.raises(ConfigSyntaxError) as raised:
            load(text)
        return raised.value

    return _refuse


VALID = """\
schema_version: 1
project_root: ..
checks:
  - id: ruff
    package_type: uvx
    package: "ruff==0.16.7"
    command: ruff
    args:
      - check
"""


# Reading the file


def test_a_valid_document_loads(load: Callable[[str], Config]) -> None:
    config = load(VALID)

    assert config.schema_version == 1
    assert config.checks[0].id == "ruff"
    assert config.checks[0].package_type is PackageType.UVX


@pytest.mark.parametrize("text", ["", "# only a comment\n", "---\n", "\n\n"])
def test_a_document_with_nothing_in_it_is_rejected(
    text: str,
    refuse: Callable[[str], ConfigSyntaxError],
) -> None:
    """An empty document parses to ``None``, so it fails the mapping check."""
    assert "found NoneType" in str(refuse(text))


@pytest.mark.parametrize("text", ["- a\n- b\n", "just a string\n", "42\n"])
def test_a_root_that_is_not_a_mapping_is_rejected(
    text: str,
    refuse: Callable[[str], ConfigSyntaxError],
) -> None:
    assert "expected a mapping at the top level" in str(refuse(text))


def test_more_than_one_document_is_rejected(
    refuse: Callable[[str], ConfigSyntaxError],
) -> None:
    error = refuse("schema_version: 1\n---\nschema_version: 1\n")

    # PyYAML refuses the second document itself, and says where both start.
    assert "expected a single document" in str(error)
    assert "line 2" in str(error)


def test_yaml_that_does_not_parse_is_rejected(
    refuse: Callable[[str], ConfigSyntaxError],
) -> None:
    error = refuse("checks: [1, 2\n")

    assert "while parsing a flow sequence" in str(error)
    assert "line 1" in str(error)


def test_a_position_names_the_config_rather_than_the_string_it_came_from(
    refuse: Callable[[str], ConfigSyntaxError],
    tmp_path: Path,
) -> None:
    """PyYAML is handed the stream, so its own marks carry the file name."""
    error = refuse("checks: [1, 2\n")

    assert "<unicode string>" not in str(error)
    assert str(tmp_path / "checksmith.yaml") in str(error)


def test_a_config_that_is_not_there_is_rejected(tmp_path: Path) -> None:
    absent = tmp_path / "absent.yaml"

    with pytest.raises(ConfigSyntaxError) as raised:
        Config.from_path(config_file=absent, working_directory=tmp_path)

    assert "No such file or directory" in str(raised.value)
    assert str(absent) in str(raised.value)


def test_a_config_that_cannot_be_read_is_rejected(tmp_path: Path) -> None:
    """Split from the missing case: this one is there, and still unusable.

    A directory is the reliable way to provoke it --- ``--config .checksmith``
    is a habit people will bring from pointing at the directory.
    """
    with pytest.raises(ConfigSyntaxError) as raised:
        Config.from_path(config_file=tmp_path, working_directory=tmp_path)

    assert "Is a directory" in str(raised.value)


def test_a_config_that_is_not_utf_8_is_rejected(tmp_path: Path) -> None:
    """Decoding happens as PyYAML reads, so this is not an ``OSError``."""
    path = tmp_path / "checksmith.yaml"
    path.write_bytes(b"schema_version: \xff\xfe\n")

    with pytest.raises(ConfigSyntaxError) as raised:
        Config.from_path(config_file=path, working_directory=tmp_path)

    assert "codec can't decode" in str(raised.value)


def test_a_duplicate_key_keeps_the_last_value(
    load: Callable[[str], Config],
    tmp_path: Path,
) -> None:
    """Deliberate: rejecting duplicates was dropped as more machinery than it earned.

    PyYAML resolves a repeated key to the last one silently. Recorded here so the
    behaviour reads as a decision rather than as something nobody noticed.
    """
    text = VALID.replace("project_root: ..\n", "project_root: ..\nproject_root: .\n")

    # ``.`` is the second value; ``..`` would have named a directory higher.
    assert load(text).project_root == tmp_path


# Schema validation


def test_a_minimal_config_parses() -> None:
    config = parse(document())

    assert config.schema_version == 1
    assert config.checks[0].id == "ruff"


def test_an_empty_argument_list_is_allowed() -> None:
    assert parse(document(checks=[check(args=[])])).checks[0].args == ()


def test_checks_keep_the_order_they_are_declared_in() -> None:
    body = document(
        checks=[
            check(id="first"),
            check(id="second", package_type="npx", package="prettier@3.6.2"),
        ]
    )

    assert tuple(item.id for item in parse(body).checks) == ("first", "second")


@pytest.mark.parametrize("version", [True, False, "1", 1.0, 2, 0, -1, None])
def test_only_the_integer_one_is_an_acceptable_schema_version(version: Any) -> None:
    """``True == 1`` in Python, so a boolean has to be refused on purpose."""
    assert "schema_version" in fields_of(reject(document(schema_version=version)))


def test_a_missing_schema_version_is_rejected() -> None:
    assert "schema_version" in fields_of(reject(document(schema_version=...)))


def test_a_config_without_checks_is_rejected() -> None:
    assert "checks" in fields_of(reject(document(checks=...)))


def test_a_config_with_no_checks_in_it_is_rejected() -> None:
    error = reject(document(checks=[]))

    assert "checks" in fields_of(error)
    assert "at least one check" in str(error)


def test_one_broken_check_is_not_also_reported_as_no_checks() -> None:
    """``Field(min_length=1)`` would add a phantom "0 items" to the real complaint."""
    assert fields_of(reject(document(checks=[check(id="")]))) == ("checks.0.id",)


@pytest.mark.parametrize(
    ("check_ids", "duplicate_id"),
    [
        (("ruff", "ruff"), "ruff"),
        (("ruff", "ty", "ruff"), "ruff"),
        (("ruff", "ty", "ty"), "ty"),
    ],
)
def test_check_ids_must_be_unique(
    check_ids: tuple[str, ...],
    duplicate_id: str,
) -> None:
    body = document(checks=[check(id=check_id) for check_id in check_ids])

    error = reject(body)

    assert fields_of(error) == ("checks",)
    assert f"duplicate check ID {duplicate_id!r}" in str(error)
    assert "IDs must be unique" in str(error)


def test_check_ids_are_case_sensitive() -> None:
    body = document(checks=[check(id="ruff"), check(id="Ruff")])

    assert tuple(item.id for item in parse(body).checks) == ("ruff", "Ruff")


@pytest.mark.parametrize("package_type", ["pipx", "", 1, None])
def test_an_unsupported_package_type_is_rejected(package_type: Any) -> None:
    body = document(checks=[check(package_type=package_type)])

    assert "checks.0.package_type" in fields_of(reject(body))


@pytest.mark.parametrize("field", ["id", "package"])
def test_an_empty_required_string_is_rejected(field: str) -> None:
    body = document(checks=[check(**{field: ""})])

    assert f"checks.0.{field}" in fields_of(reject(body))


@pytest.mark.parametrize("command", ["eslint", "Ruff", "ruff ", ""])
def test_an_unsupported_command_is_rejected(command: str) -> None:
    """``command`` names the class that will read the tool's output, so a tool
    Checksmith cannot read is a config error rather than a surprise mid-run.
    """
    body = document(checks=[check(command=command)])

    assert "checks.0.command" in fields_of(reject(body))


@pytest.mark.parametrize("value", [1, True, None, ["a"]])
def test_a_field_of_the_wrong_type_is_not_coerced(value: Any) -> None:
    body = document(checks=[check(command=value)])

    assert "checks.0.command" in fields_of(reject(body))


def test_arguments_must_be_strings_or_config_paths() -> None:
    """Both branches are reported, because the value satisfies neither."""
    body = document(checks=[check(args=["check", 2])])

    assert fields_of(reject(body)) == (
        "checks.0.args.1.str",
        "checks.0.args.1.ConfigPath",
    )


def test_arguments_are_required() -> None:
    assert "checks.0.args" in fields_of(reject(document(checks=[check(args=...)])))


@pytest.mark.parametrize(
    ("package_type", "package"),
    [
        ("uvx", "ruff"),
        ("uvx", "prettier@3.6.2"),
        ("npx", "prettier"),
        ("npx", "prettier@latest"),
        ("npx", "prettier@*"),
        ("npx", "ruff==0.16.7"),
    ],
)
def test_a_package_that_names_no_version_is_rejected(
    package_type: str,
    package: str,
) -> None:
    body = document(checks=[check(package_type=package_type, package=package)])

    assert "checks.0.package" in fields_of(reject(body))


@pytest.mark.parametrize(
    ("package_type", "package"),
    [
        ("uvx", "ruff>=0.14,<0.15"),
        ("uvx", "mypy[faster-cache]==1.18.0"),
        ("npx", "prettier@^3.6.2"),
        ("npx", "prettier@1.2.3 || >=2.0.0"),
    ],
)
def test_a_package_at_a_range_is_accepted(package_type: str, package: str) -> None:
    """A range is a constraint, so a config file may name one instead of a pin."""
    body = document(checks=[check(package_type=package_type, package=package)])

    assert parse(body).checks[0].package == package


def test_a_package_is_checked_against_the_package_type_that_will_consume_it() -> None:
    npm = document(checks=[check(package_type="npx", package="prettier@3.6.2")])

    assert parse(npm).checks[0].package == "prettier@3.6.2"


def test_a_uv_check_requires_an_explicit_null_package() -> None:
    body = document(checks=[check(package_type="uv", package=...)])

    assert fields_of(reject(body)) == ("checks.0.package",)


@pytest.mark.parametrize("package", ["pytest", "pytest==9.0.2", ""])
def test_a_uv_check_rejects_a_package_specification(package: str) -> None:
    body = document(checks=[check(package_type="uv", package=package)])

    assert fields_of(reject(body)) == ("checks.0.package",)


@pytest.mark.parametrize("package_type", ["uvx", "npx"])
def test_an_isolated_check_rejects_a_null_package(package_type: str) -> None:
    body = document(checks=[check(package_type=package_type, package=None)])

    assert fields_of(reject(body)) == ("checks.0.package",)


@pytest.mark.parametrize(
    ("package_type", "package"),
    [("uvx", "pytest==9.0.2"), ("npx", "pytest@9.0.2")],
)
def test_pytest_requires_the_uv_project_environment(
    package_type: str,
    package: str,
) -> None:
    body = document(
        checks=[check(package_type=package_type, package=package, command="pytest")]
    )

    error = reject(body)

    assert fields_of(error) == ("checks.0.command",)
    assert "pytest requires package_type: uv" in str(error)


def test_an_invalid_pytest_package_type_reports_only_the_type() -> None:
    body = document(checks=[check(package_type="pipx", package=None, command="pytest")])

    assert fields_of(reject(body)) == ("checks.0.package_type",)


def test_an_unusable_package_type_suppresses_the_package_complaint() -> None:
    """With no package type there is no notion of a correct package; fix the type."""
    body = document(checks=[check(package_type="pipx", package="anything at all")])

    assert fields_of(reject(body)) == ("checks.0.package_type",)


def test_every_violation_from_one_pass_is_reported() -> None:
    body = document(schema_version=2, checks=[check(package_type="pipx", id="")])

    assert set(fields_of(reject(body))) == {
        "schema_version",
        "checks.0.id",
        "checks.0.package_type",
    }


def test_a_violation_names_the_field_it_came_from() -> None:
    error = reject(document(schema_version=2))

    assert error.violations[0].field == "schema_version"
    assert "schema_version:" in str(error)


def test_a_rejection_names_the_config_it_came_from() -> None:
    assert str(CONFIG_PATH) in str(reject(document(schema_version=2)))


@pytest.mark.parametrize(
    "body",
    [
        {"flag": True},
        {"flag": None},
        {"ratio": 1.5},
        {"count": 7},
        {"name": "ruff"},
        {"items": [1, 2]},
    ],
)
def test_the_reader_judges_no_types_and_the_schema_does(
    body: Mapping[object, Any],
) -> None:
    """Nothing is refused for its YAML type; the schema gives a better message."""
    assert isinstance(reject(body), ConfigSchemaError)


def test_a_value_yaml_implicitly_retyped_is_rejected() -> None:
    """``args: [2021-01-01]`` reaches the schema as a ``date``."""
    body = document(checks=[check(args=[date(2021, 1, 1)])])

    assert fields_of(reject(body)) == (
        "checks.0.args.0.str",
        "checks.0.args.0.ConfigPath",
    )


@pytest.mark.parametrize("value", [{"a"}, (1, 2)])
def test_a_value_of_a_type_yaml_can_produce_but_a_config_cannot_use(
    value: object,
) -> None:
    """``!!set`` constructs without complaint under ``SafeLoader``."""
    body = document(checks=[check(args=[value])])

    assert fields_of(reject(body)) == (
        "checks.0.args.0.str",
        "checks.0.args.0.ConfigPath",
    )


def test_a_self_referential_document_is_rejected_without_recursing() -> None:
    """A recursive anchor builds a cycle; the schema has finite depth, so it ends.

    A mapping is a candidate ``ConfigPath``, so the cycle is read one level down
    and stops there: the key it needs is absent, and the key it has is refused.
    """
    loop: dict[str, Any] = {}
    loop["inner"] = loop

    assert fields_of(reject(document(checks=[check(args=[loop])]))) == (
        "checks.0.args.0.str",
        "checks.0.args.0.ConfigPath.config_path",
        "checks.0.args.0.ConfigPath.inner",
    )


# The project root


def test_a_config_naming_no_project_root_runs_where_the_config_lives() -> None:
    """The default ``.`` is relative to the config file, like every other path."""
    assert parse(document()).project_root == CONFIG_PATH.parent


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("..", "/project"),
        ("./absent", "/project/.checksmith/absent"),
        ("/elsewhere", "/elsewhere"),
        ("/a/b/../c", "/a/c"),
    ],
)
def test_a_project_root_resolves_against_the_directory_holding_the_config(
    value: str,
    expected: str,
) -> None:
    """Everything in a config file is relative to that file, this included.

    Resolved lexically: ``..`` is collapsed without following symlinks, an
    absolute root survives untouched, and a directory that is not there still
    resolves --- nothing is asked of the filesystem.
    """
    assert parse(document(project_root=value)).project_root == Path(expected)


def test_a_config_built_without_the_path_as_context_is_refused() -> None:
    """There is nothing to resolve a root against, so the root would be a guess.

    A ``TypeError`` rather than a schema violation: this is a caller building a
    ``Config`` outside ``from_mapping``, not a line anybody can fix in YAML.
    """
    with pytest.raises(TypeError, match="Config.from_mapping"):
        Config.model_validate(document())


# Arguments


def arguments_of_all(args: list[object]) -> tuple[Argument, ...]:
    """Parse a config file declaring these arguments, and return what came back."""
    return parse(document(checks=[check(args=args)])).checks[0].args


def arguments_of(argument: object) -> tuple[Argument, ...]:
    """Parse a config file declaring one argument, and return what came back."""
    return arguments_of_all([argument])


@pytest.mark.parametrize(
    "argument",
    [
        "{files.config}",
        "{not_files}",
        "{}",
        "${HOME}",
        "$HOME",
        "*.py",
        "~/somewhere",
        "a && b",
        "{FILES.config}",
        "{file.config}",
        "{files.}",
        "{files.config",
        "--config={files.config",
        "{files.1bad}",
        "{files.a b}",
        "{files.a.b}",
        "--flag={value}",
        "files.config",
        "{{other.config}}",
        "check",
        "./src",
        "tests/*",
    ],
)
def test_an_argument_is_passed_through_literally(argument: str) -> None:
    """No substitution, no shell interpolation, no ``~``, and no globbing."""
    assert arguments_of(argument) == (argument,)


def test_an_argument_containing_spaces_stays_one_argument() -> None:
    resolved = arguments_of("--config=/my project/.checksmith/ruff.toml")

    assert resolved == ("--config=/my project/.checksmith/ruff.toml",)
    assert len(resolved) == 1


# Config paths


def config_path_of(value: object) -> Path:
    """Parse a config file declaring one ``config_path`` argument."""
    argument = arguments_of({"config_path": value})[0]
    assert isinstance(argument, ConfigPath)
    return argument.config_path


def test_a_config_path_resolves_against_the_config_directory() -> None:
    """The rule ``project_root`` follows, applied to an argument.

    This is what the whole arrangement buys: the file is named as it sits on
    disk, beside the config file, rather than by a route out to the project
    root and back.
    """
    assert config_path_of("ruff.toml") == CONFIG_PATH.parent / "ruff.toml"


def test_a_config_path_is_absolute_so_the_project_root_cannot_change_it() -> None:
    """The tool runs in the project root; an absolute path is immune to it."""
    assert config_path_of("ruff.toml").is_absolute()


def test_a_config_path_may_climb_out_of_the_config_directory() -> None:
    """Normalised lexically, as ``project_root`` is, and not against the disk."""
    assert config_path_of("../shared/ruff.toml") == Path("/project/shared/ruff.toml")


def test_a_config_path_that_is_already_absolute_is_left_alone() -> None:
    assert config_path_of("/etc/checksmith/ruff.toml") == Path(
        "/etc/checksmith/ruff.toml"
    )


def test_a_config_path_reaches_the_vector_as_a_plain_string() -> None:
    """Whatever the schema models it as, a process is handed strings."""
    check_ = parse(
        document(checks=[check(args=["--config", {"config_path": "ruff.toml"}])])
    ).checks[0]

    assert check_.argv[-1] == "/project/.checksmith/ruff.toml"
    assert all(isinstance(item, str) for item in check_.argv)


def test_resolved_arguments_can_be_used_without_the_package_runner() -> None:
    check_ = parse(
        document(
            checks=[check(args=["--config", {"config_path": "ruff.toml"}, "a path"])]
        )
    ).checks[0]

    assert check_.arguments == (
        "--config",
        "/project/.checksmith/ruff.toml",
        "a path",
    )
    assert "arguments" not in Check.model_fields


def test_bare_arguments_beside_a_config_path_are_still_passed_through() -> None:
    """The two forms coexist: only the marked one is resolved.

    ``.`` is the case that matters. It is a path too, but it is the tool's to
    read against the project root, and resolving it would change what is
    scanned.
    """
    arguments = arguments_of_all(
        ["check", "--config", {"config_path": "ruff.toml"}, "."]
    )

    assert arguments[0] == "check"
    assert arguments[1] == "--config"
    assert isinstance(arguments[2], ConfigPath)
    assert arguments[3] == "."


def test_a_config_path_with_an_unknown_key_is_rejected() -> None:
    """A misspelling must not read as a mapping with the real key missing."""
    body = document(checks=[check(args=[{"config_path": "ruff.toml", "extra": 1}])])

    assert "checks.0.args.0.ConfigPath.extra" in fields_of(reject(body))


def test_a_config_path_naming_no_path_is_rejected() -> None:
    body = document(checks=[check(args=[{}])])

    assert "checks.0.args.0.ConfigPath.config_path" in fields_of(reject(body))


def test_a_config_path_cannot_be_built_without_the_config_file() -> None:
    """A ``TypeError``, not a violation: there is no line for a user to edit.

    The same refusal :attr:`Config.project_root` makes, for the same reason ---
    an unresolved config path is a half-built object, not a bad config file.
    """
    with pytest.raises(TypeError, match="config path as context"):
        ConfigPath.model_validate({"config_path": "ruff.toml"})


# The argument vector


def resolved(**overrides: Any) -> Check:
    """One check, built the only way a check can be built."""
    return parse(document(checks=[check(**overrides)])).checks[0]


def test_a_uvx_check_builds_the_documented_vector() -> None:
    assert resolved(args=["check", "--config", "./ruff.toml", "."]).argv == (
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
    assert configured.argv == ("uv", "run", "--locked", "pytest", "tests")


def test_other_commands_can_use_the_uv_project_environment() -> None:
    configured = resolved(package_type="uv", package=None, args=["check", "."])

    assert configured.argv == ("uv", "run", "--locked", "ruff", "check", ".")


def test_a_semgrep_check_is_accepted_and_builds_a_uvx_vector() -> None:
    configured = resolved(
        id="function-style",
        package="semgrep==1.176.1",
        command="semgrep",
        args=["scan", "--json", "--config", ".checksmith/semgrep.yaml", "."],
    )

    assert configured.command is CommandName.SEMGREP
    assert configured.argv == (
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


def test_a_ty_check_is_accepted_and_builds_a_uvx_vector() -> None:
    configured = resolved(
        id="type-check",
        package="ty==0.0.80",
        command="ty",
        args=["check", "--output-format", "gitlab", "--error-on-warning", "."],
    )

    assert configured.id == "type-check"
    assert configured.command is CommandName.TY
    assert configured.argv == (
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


def test_pyarchgraph_configuration_passes_source_and_rules_unchanged() -> None:
    configured = resolved(
        command="pyarchgraph",
        package="pyarchgraph==0.5.0",
        args=["src", "--exclude", "vendor/*", "--forbid", "app:service"],
    )
    assert configured.command is CommandName.PYARCHGRAPH
    assert configured.argv == (
        "uvx",
        "--from",
        "pyarchgraph==0.5.0",
        "pyarchgraph",
        "src",
        "--exclude",
        "vendor/*",
        "--forbid",
        "app:service",
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

    assert configured.argv[:4] == ("uvx", "--from", package, "pyarchgraph")


def test_a_check_with_no_arguments_still_builds_a_runnable_vector() -> None:
    assert resolved(args=[]).argv == ("uvx", "--from", "ruff==0.16.7", "ruff")


def test_an_argument_containing_spaces_stays_one_element() -> None:
    """The vector is never joined into a string, so nothing needs quoting."""
    vector = resolved(args=["--config", "/my project/ruff.toml"]).argv

    assert vector[-1] == "/my project/ruff.toml"


def test_the_vector_is_derived_rather_than_declared() -> None:
    """A property, not a field: the config file's schema must not grow one."""
    assert "argv" not in Check.model_fields
    assert "argv" not in resolved().model_dump()


# Loading from a path


def test_a_relative_option_resolves_against_the_working_directory(
    config_tree: Path,
) -> None:
    config = Config.from_path(
        config_file=Path(".checksmith/checksmith.yaml"),
        working_directory=config_tree,
    )

    assert config.project_root == config_tree
    assert tuple(item.id for item in config.checks) == ("ruff",)


def test_an_absolute_option_gives_the_same_result_from_anywhere(
    config_file: Path,
    config_tree: Path,
    tmp_path: Path,
) -> None:
    elsewhere = tmp_path / "elsewhere" / "deeper"
    elsewhere.mkdir(parents=True)

    from_project = Config.from_path(
        config_file=config_file,
        working_directory=config_tree,
    )
    from_elsewhere = Config.from_path(
        config_file=config_file,
        working_directory=elsewhere,
    )

    assert from_project == from_elsewhere


def test_a_config_that_declares_no_project_root_loads(
    tmp_path: Path,
) -> None:
    (tmp_path / "checksmith.yaml").write_text(
        "schema_version: 1\n"
        "checks:\n"
        "  - id: ruff\n"
        "    package_type: uvx\n"
        '    package: "ruff==0.16.7"\n'
        "    command: ruff\n"
        '    args: ["check", "--config", "./ruff.toml", "."]\n',
        encoding="utf-8",
    )

    config = Config.from_path(
        config_file=tmp_path / "checksmith.yaml",
        working_directory=tmp_path,
    )

    assert config.project_root == tmp_path


def test_the_arguments_arrive_as_the_config_file_wrote_them(
    config_file: Path,
    config_tree: Path,
) -> None:
    """Every argument but the config path is carried through untouched.

    ``.`` in particular: it is the tool's to read against the project root it
    runs in, and resolving it here would change what the check scans.
    """
    config = Config.from_path(config_file=config_file, working_directory=config_tree)
    arguments = config.checks[0].args

    assert arguments[:2] == ("check", "--config")
    assert arguments[2] == ConfigPath.model_validate(
        {"config_path": "ruff.toml"}, context=config_file
    )
    assert arguments[3:] == ("--output-format", "json", ".")
    assert config.checks[0].package_type is PackageType.UVX


def test_a_sibling_file_nobody_listed_does_not_become_a_check(
    config_file: Path,
    config_tree: Path,
) -> None:
    """Only config entries enable checks; the directory is not scanned."""
    (config_tree / ".checksmith" / "mypy.ini").write_text("", encoding="utf-8")
    (config_tree / ".checksmith" / "prettier.json").write_text("", encoding="utf-8")

    config = Config.from_path(config_file=config_file, working_directory=config_tree)

    assert tuple(item.id for item in config.checks) == ("ruff",)


def test_a_supporting_path_that_does_not_exist_still_loads(
    config_file: Path,
    config_tree: Path,
) -> None:
    """Checksmith carries paths through; it does not check them.

    A tool handed a path that is not there produces its own diagnostic, which is
    a better one than Checksmith could invent on its behalf.
    """
    (config_tree / ".checksmith" / "ruff.toml").unlink()

    config = Config.from_path(config_file=config_file, working_directory=config_tree)

    assert config.checks[0].argv[-4] == str(config_tree / ".checksmith" / "ruff.toml")


def test_loading_leaves_native_configurations_byte_for_byte_unchanged(
    config_file: Path,
    config_tree: Path,
) -> None:
    """Checksmith passes files in place; it never rewrites or relocates them."""
    watched = (
        config_tree / ".checksmith" / "ruff.toml",
        config_tree / ".checksmith" / "checksmith.yaml",
    )
    before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in watched}

    Config.from_path(config_file=config_file, working_directory=config_tree)

    after = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in watched}
    assert after == before
