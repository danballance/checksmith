from pathlib import Path

import pytest

from astcheck.adapters.configuration import YamlConfigurationLoader
from astcheck.domain.errors import AstcheckError

CONFIGURATION = """schema_version: 1
project_root: ..
sources: [src]
exclusions: [src/generated/**]
plugins:
  - id: custom
    settings:
      threshold: 3
"""


def test_root_resolves_relative_to_configuration_independently_of_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_directory = tmp_path / "configuration"
    config_directory.mkdir()
    path = config_directory / "astcheck.yaml"
    path.write_text(CONFIGURATION, encoding="utf-8")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    configuration = YamlConfigurationLoader().load(path=path)

    assert configuration.project_root == tmp_path
    assert configuration.sources == ("src",)
    assert configuration.exclusions == ("src/generated/**",)
    assert configuration.plugins[0].id == "custom"
    assert configuration.plugins[0].settings == {"threshold": 3}


@pytest.mark.parametrize(
    "content",
    [
        "",
        "42",
        "[]",
        "sources: [",
        CONFIGURATION.replace("schema_version: 1", "schema_version: true"),
        CONFIGURATION.replace("schema_version: 1", "schema_version: 2"),
        CONFIGURATION.replace("sources: [src]", "sources: [../src]"),
        CONFIGURATION.replace("project_root: ..", "project_root: absent"),
        CONFIGURATION.replace("project_root: ..", "project_root: astcheck.yaml"),
        CONFIGURATION + "unexpected: true\n",
    ],
)
def test_invalid_configuration_reports_its_file_and_cause(
    tmp_path: Path, content: str
) -> None:
    path = tmp_path / "astcheck.yaml"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(AstcheckError, match="Could not load configuration") as raised:
        YamlConfigurationLoader().load(path=path)

    assert str(path) in str(raised.value)
    assert raised.value.__cause__ is not None


def test_missing_configuration_is_an_analysis_error(tmp_path: Path) -> None:
    with pytest.raises(AstcheckError, match="missing.yaml"):
        YamlConfigurationLoader().load(path=tmp_path / "missing.yaml")


def test_invalid_configuration_encoding_is_an_analysis_error(tmp_path: Path) -> None:
    path = tmp_path / "astcheck.yaml"
    path.write_bytes(b"\xff")

    with pytest.raises(AstcheckError, match="Could not load configuration"):
        YamlConfigurationLoader().load(path=path)
