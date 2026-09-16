"""Tests for :mod:`checksmith.config`."""

import hashlib
from collections.abc import Callable, Mapping
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from checksmith.config import Config
from checksmith.errors import ConfigSchemaError, ConfigSyntaxError
from checksmith.dtos import RunnerName

CONFIG_PATH = Path("/project/.checksmith/checksmith.yaml")


def check(**overrides: Any) -> dict[str, Any]:
    """A minimal valid check, with fields replaced or removed by keyword."""
    body: dict[str, Any] = {
        "id": "ruff",
        "runner": "uvx",
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
    return Config.from_mapping(document=body, config_path=CONFIG_PATH)


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
        return Config.from_path(config_path=path, working_directory=tmp_path)

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
    runner: uvx
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
    assert config.checks[0].runner is RunnerName.UVX


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
        Config.from_path(config_path=absent, working_directory=tmp_path)

    assert "No such file or directory" in str(raised.value)
    assert str(absent) in str(raised.value)


def test_a_config_that_cannot_be_read_is_rejected(tmp_path: Path) -> None:
    """Split from the missing case: this one is there, and still unusable.

    A directory is the reliable way to provoke it --- ``--config .checksmith``
    is a habit people will bring from pointing at the directory.
    """
    with pytest.raises(ConfigSyntaxError) as raised:
        Config.from_path(config_path=tmp_path, working_directory=tmp_path)

    assert "Is a directory" in str(raised.value)


def test_a_config_that_is_not_utf_8_is_rejected(tmp_path: Path) -> None:
    """Decoding happens as PyYAML reads, so this is not an ``OSError``."""
    path = tmp_path / "checksmith.yaml"
    path.write_bytes(b"schema_version: \xff\xfe\n")

    with pytest.raises(ConfigSyntaxError) as raised:
        Config.from_path(config_path=path, working_directory=tmp_path)

    assert "codec can't decode" in str(raised.value)


def test_a_duplicate_key_keeps_the_last_value(
    load: Callable[[str], Config],
) -> None:
    """Deliberate: rejecting duplicates was dropped as more machinery than it earned.

    PyYAML resolves a repeated key to the last one silently. Recorded here so the
    behaviour reads as a decision rather than as something nobody noticed.
    """
    text = VALID.replace("project_root: ..\n", "project_root: ..\nproject_root: .\n")

    # ``.`` is the second value; ``..`` would have named a directory higher.
    assert load(text).project_root == Path(".")


def test_an_alias_reused_between_siblings_is_fine(
    load: Callable[[str], Config],
) -> None:
    text = (
        "schema_version: 1\n"
        "checks:\n"
        "  - id: first\n"
        "    runner: uvx\n"
        '    package: "ruff==0.16.7"\n'
        "    command: ruff\n"
        "    args: &shared [check]\n"
        "  - id: second\n"
        "    runner: uvx\n"
        '    package: "mypy==1.18.0"\n'
        "    command: mypy\n"
        "    args: *shared\n"
    )

    config = load(text)

    assert config.checks[0].args == config.checks[1].args == ("check",)


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
            check(id="second", runner="npx", package="prettier@3.6.2"),
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


def test_two_checks_may_share_an_id() -> None:
    """Dropped as more machinery than it earned: the output shows two like rows."""
    body = document(checks=[check(id="ruff"), check(id="ruff")])

    assert tuple(item.id for item in parse(body).checks) == ("ruff", "ruff")


@pytest.mark.parametrize("runner", ["uv", "pipx", "", 1, None])
def test_an_unsupported_runner_is_rejected(runner: Any) -> None:
    body = document(checks=[check(runner=runner)])

    assert "checks.0.runner" in fields_of(reject(body))


@pytest.mark.parametrize("field", ["id", "package", "command"])
def test_an_empty_required_string_is_rejected(field: str) -> None:
    body = document(checks=[check(**{field: ""})])

    assert f"checks.0.{field}" in fields_of(reject(body))


@pytest.mark.parametrize("value", [1, True, None, ["a"]])
def test_a_field_of_the_wrong_type_is_not_coerced(value: Any) -> None:
    body = document(checks=[check(command=value)])

    assert "checks.0.command" in fields_of(reject(body))


def test_arguments_must_be_strings() -> None:
    body = document(checks=[check(args=["check", 2])])

    assert "checks.0.args.1" in fields_of(reject(body))


def test_arguments_are_required() -> None:
    assert "checks.0.args" in fields_of(reject(document(checks=[check(args=...)])))


@pytest.mark.parametrize(
    ("runner", "package"),
    [
        ("uvx", "ruff"),
        ("uvx", "prettier@3.6.2"),
        ("npx", "prettier"),
        ("npx", "prettier@latest"),
        ("npx", "prettier@*"),
        ("npx", "ruff==0.16.7"),
    ],
)
def test_a_package_that_names_no_version_is_rejected(runner: str, package: str) -> None:
    body = document(checks=[check(runner=runner, package=package)])

    assert "checks.0.package" in fields_of(reject(body))


@pytest.mark.parametrize(
    ("runner", "package"),
    [
        ("uvx", "ruff>=0.14,<0.15"),
        ("uvx", "mypy[faster-cache]==1.18.0"),
        ("npx", "prettier@^3.6.2"),
        ("npx", "prettier@1.2.3 || >=2.0.0"),
    ],
)
def test_a_package_at_a_range_is_accepted(runner: str, package: str) -> None:
    """A range is a constraint, so a config file may name one instead of a pin."""
    body = document(checks=[check(runner=runner, package=package)])

    assert parse(body).checks[0].package == package


def test_a_package_is_checked_against_the_runner_that_will_consume_it() -> None:
    npm = document(checks=[check(runner="npx", package="prettier@3.6.2")])

    assert parse(npm).checks[0].package == "prettier@3.6.2"


def test_an_unusable_runner_suppresses_the_package_complaint() -> None:
    """With no runner there is no notion of a correct package; fix the runner."""
    body = document(checks=[check(runner="pipx", package="anything at all")])

    assert fields_of(reject(body)) == ("checks.0.runner",)


def test_every_violation_from_one_pass_is_reported() -> None:
    body = document(schema_version=2, checks=[check(runner="pipx", id="")])

    assert set(fields_of(reject(body))) == {
        "schema_version",
        "checks.0.id",
        "checks.0.runner",
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

    assert fields_of(reject(body)) == ("checks.0.args.0",)


@pytest.mark.parametrize("value", [{"a"}, (1, 2)])
def test_a_value_of_a_type_yaml_can_produce_but_a_config_cannot_use(
    value: object,
) -> None:
    """``!!set`` constructs without complaint under ``SafeLoader``."""
    body = document(checks=[check(args=[value])])

    assert fields_of(reject(body)) == ("checks.0.args.0",)


def test_a_self_referential_document_is_rejected_without_recursing() -> None:
    """A recursive anchor builds a cycle; the schema has finite depth, so it ends."""
    loop: dict[str, Any] = {}
    loop["inner"] = loop

    assert fields_of(reject(document(checks=[check(args=[loop])]))) == (
        "checks.0.args.0",
    )


# The project root


def test_the_project_root_defaults_to_the_current_directory() -> None:
    assert parse(document()).project_root == Path(".")


@pytest.mark.parametrize("value", ["..", "./absent", "/elsewhere", "/a/b/../c"])
def test_a_project_root_is_kept_as_the_config_file_wrote_it(value: str) -> None:
    """Nothing is resolved or collapsed, and nothing is asked of the filesystem."""
    assert parse(document(project_root=value)).project_root == Path(value)


# Arguments


def arguments_of(argument: str) -> tuple[str, ...]:
    """Parse a config file declaring one argument, and return what came back."""
    return parse(document(checks=[check(args=[argument])])).checks[0].args


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


# Loading from a path


def test_a_relative_option_resolves_against_the_working_directory(
    config_tree: Path,
) -> None:
    config = Config.from_path(
        config_path=Path(".checksmith/checksmith.yaml"),
        working_directory=config_tree,
    )

    assert config.project_root == Path("..")
    assert tuple(item.id for item in config.checks) == ("ruff",)


def test_an_absolute_option_gives_the_same_result_from_anywhere(
    config_path: Path,
    config_tree: Path,
    tmp_path: Path,
) -> None:
    elsewhere = tmp_path / "elsewhere" / "deeper"
    elsewhere.mkdir(parents=True)

    from_project = Config.from_path(
        config_path=config_path,
        working_directory=config_tree,
    )
    from_elsewhere = Config.from_path(
        config_path=config_path,
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
        "    runner: uvx\n"
        '    package: "ruff==0.16.7"\n'
        "    command: ruff\n"
        '    args: ["check", "--config", "./ruff.toml", "."]\n',
        encoding="utf-8",
    )

    config = Config.from_path(
        config_path=tmp_path / "checksmith.yaml",
        working_directory=tmp_path,
    )

    assert config.project_root == Path(".")


def test_the_arguments_arrive_as_the_config_file_wrote_them(
    config_path: Path,
    config_tree: Path,
) -> None:
    config = Config.from_path(config_path=config_path, working_directory=config_tree)

    assert config.checks[0].args == ("check", "--config", "./ruff.toml", ".")
    assert config.checks[0].runner is RunnerName.UVX


def test_a_sibling_file_nobody_listed_does_not_become_a_check(
    config_path: Path,
    config_tree: Path,
) -> None:
    """Only config entries enable checks; the directory is not scanned."""
    (config_tree / ".checksmith" / "mypy.ini").write_text("", encoding="utf-8")
    (config_tree / ".checksmith" / "prettier.json").write_text("", encoding="utf-8")

    config = Config.from_path(config_path=config_path, working_directory=config_tree)

    assert tuple(item.id for item in config.checks) == ("ruff",)


def test_a_supporting_path_that_does_not_exist_still_loads(
    config_path: Path,
    config_tree: Path,
) -> None:
    """Checksmith carries paths through; it does not check them.

    A tool handed a path that is not there produces its own diagnostic, which is
    a better one than Checksmith could invent on its behalf.
    """
    (config_tree / ".checksmith" / "ruff.toml").unlink()

    config = Config.from_path(config_path=config_path, working_directory=config_tree)

    assert "./ruff.toml" in config.checks[0].args


def test_loading_leaves_native_configurations_byte_for_byte_unchanged(
    config_path: Path,
    config_tree: Path,
) -> None:
    """Checksmith passes files in place; it never rewrites or relocates them."""
    watched = (
        config_tree / ".checksmith" / "ruff.toml",
        config_tree / ".checksmith" / "checksmith.yaml",
    )
    before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in watched}

    Config.from_path(config_path=config_path, working_directory=config_tree)

    after = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in watched}
    assert after == before
