import hashlib
from collections.abc import Callable, Mapping
from pathlib import Path

import pytest

from checksmith.adapters.driven.configuration.loading import (
    ConfigLoader,
    LocalYamlConfigSource,
)
from checksmith.domain.config import Config, ConfigPath
from checksmith.domain.errors import ConfigSchemaError, ConfigSyntaxError
from checksmith.domain.models import PackageType


@pytest.fixture
def load(tmp_path: Path) -> Callable[[str], Config]:
    """Write a config file and load it through the real entry point."""

    def _load(text: str) -> Config:
        path = tmp_path / "checksmith.yaml"
        path.write_text(text, encoding="utf-8")
        return ConfigLoader(source=LocalYamlConfigSource()).load(
            config_file=path, working_directory=tmp_path
        )

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
        ConfigLoader(source=LocalYamlConfigSource()).load(
            config_file=absent, working_directory=tmp_path
        )

    assert "No such file or directory" in str(raised.value)
    assert str(absent) in str(raised.value)


def test_a_config_that_cannot_be_read_is_rejected(tmp_path: Path) -> None:
    """Split from the missing case: this one is there, and still unusable.

    A directory is the reliable way to provoke it --- ``--config .checksmith``
    is a habit people will bring from pointing at the directory.
    """
    with pytest.raises(ConfigSyntaxError) as raised:
        ConfigLoader(source=LocalYamlConfigSource()).load(
            config_file=tmp_path, working_directory=tmp_path
        )

    assert "Is a directory" in str(raised.value)


def test_a_config_that_is_not_utf_8_is_rejected(tmp_path: Path) -> None:
    """Decoding happens as PyYAML reads, so this is not an ``OSError``."""
    path = tmp_path / "checksmith.yaml"
    path.write_bytes(b"schema_version: \xff\xfe\n")

    with pytest.raises(ConfigSyntaxError) as raised:
        ConfigLoader(source=LocalYamlConfigSource()).load(
            config_file=path, working_directory=tmp_path
        )

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


# Loading from a path


def test_a_relative_option_resolves_against_the_working_directory(
    config_tree: Path,
) -> None:
    config = ConfigLoader(source=LocalYamlConfigSource()).load(
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

    from_project = ConfigLoader(source=LocalYamlConfigSource()).load(
        config_file=config_file,
        working_directory=config_tree,
    )
    from_elsewhere = ConfigLoader(source=LocalYamlConfigSource()).load(
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

    config = ConfigLoader(source=LocalYamlConfigSource()).load(
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
    config = ConfigLoader(source=LocalYamlConfigSource()).load(
        config_file=config_file, working_directory=config_tree
    )
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

    config = ConfigLoader(source=LocalYamlConfigSource()).load(
        config_file=config_file, working_directory=config_tree
    )

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

    config = ConfigLoader(source=LocalYamlConfigSource()).load(
        config_file=config_file, working_directory=config_tree
    )

    assert config.checks[0].arguments[-4] == str(config_tree / ".checksmith" / "ruff.toml")


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

    ConfigLoader(source=LocalYamlConfigSource()).load(
        config_file=config_file, working_directory=config_tree
    )

    after = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in watched}
    assert after == before


class RecordingSource:
    def __init__(self, document: Mapping[object, object]) -> None:
        self.document = document
        self.paths: list[Path] = []

    def read(self, *, path: Path) -> Mapping[object, object]:
        self.paths.append(path)
        return self.document


def test_loader_uses_its_source_and_resolves_both_directory_bases() -> None:
    source = RecordingSource(
        document={
            "schema_version": 1,
            "project_root": "..",
            "checks": [
                {
                    "id": "ruff",
                    "package_type": "uvx",
                    "package": "ruff==0.16.7",
                    "command": "ruff",
                    "args": [{"config_path": "ruff.toml"}],
                }
            ],
        }
    )

    config = ConfigLoader(source=source).load(
        config_file=Path("other/../.checksmith/checksmith.yaml"),
        working_directory=Path("/project"),
    )

    assert source.paths == [Path("/project/.checksmith/checksmith.yaml")]
    assert config.project_root == Path("/project")
    assert config.checks[0].arguments == ("/project/.checksmith/ruff.toml",)


def test_loader_validates_documents_from_its_source() -> None:
    source = RecordingSource(document={"schema_version": 2, "checks": []})
    with pytest.raises(ConfigSchemaError):
        ConfigLoader(source=source).load(
            config_file=Path("checksmith.yaml"), working_directory=Path("/project")
        )
