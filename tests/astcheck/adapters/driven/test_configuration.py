from pathlib import Path

import pytest
import yaml

from astcheck.adapters.driven.configuration import (
    YamlConfigurationLoader,
    YamlConfigurationRenderer,
    YamlPolicyLoader,
)
from astcheck.domain.configuration import AnalysisConfiguration, AnalysisPolicy
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


def test_policy_file_preserves_explicit_paths_and_settings(
    analysis_policy: AnalysisPolicy, tmp_path: Path
) -> None:
    path = tmp_path / "policy.yaml"
    path.write_text(
        yaml.safe_dump(analysis_policy.model_dump(mode="json")), encoding="utf-8"
    )

    assert YamlPolicyLoader().load(path=path) == analysis_policy


@pytest.mark.parametrize(
    "content",
    [
        "",
        "[]",
        "sources: [",
        "sources: [src]",
        "sources: [src]\nextra: yes",
        "date: 2026-09-27",
    ],
)
def test_invalid_policy_files_fail_clearly(tmp_path: Path, content: str) -> None:
    path = tmp_path / "policy.yaml"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(AstcheckError, match="ASTcheck policy"):
        YamlPolicyLoader().load(path=path)


@pytest.mark.parametrize("content", [None, b"\xff"])
def test_unreadable_policy_files_fail_clearly(
    tmp_path: Path, content: bytes | None
) -> None:
    path = tmp_path / "policy.yaml"
    if content is not None:
        path.write_bytes(content)

    with pytest.raises(AstcheckError, match="Could not read ASTcheck policy"):
        YamlPolicyLoader().load(path=path)


def test_renderer_uses_future_config_destination_without_writing_files(
    analysis_policy: AnalysisPolicy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root = tmp_path / "source root"
    project_root.mkdir()
    config_file = tmp_path / "new/nested/astcheck.yaml"
    monkeypatch.chdir(project_root)

    text = YamlConfigurationRenderer().render(
        project_root=project_root, config_file=config_file, policy=analysis_policy
    )
    document = AnalysisConfiguration.model_validate(yaml.safe_load(text))

    assert document.project_root == Path("../../source root")
    assert document.sources == analysis_policy.sources
    assert document.plugins == analysis_policy.plugins
    assert not config_file.parent.exists()
    assert tuple(project_root.iterdir()) == ()


def test_renderer_preserves_the_selected_symlink_project_root(
    analysis_policy: AnalysisPolicy, tmp_path: Path
) -> None:
    actual_root = tmp_path / "actual"
    actual_root.mkdir()
    selected_root = tmp_path / "selected"
    selected_root.symlink_to(actual_root, target_is_directory=True)

    text = YamlConfigurationRenderer().render(
        project_root=selected_root,
        config_file=tmp_path / "configuration/astcheck.yaml",
        policy=analysis_policy,
    )
    document = AnalysisConfiguration.model_validate(yaml.safe_load(text))

    assert document.project_root == Path("../selected")


@pytest.mark.parametrize("exists", [False, True])
def test_renderer_rejects_missing_roots_and_file_roots(
    analysis_policy: AnalysisPolicy, tmp_path: Path, exists: bool
) -> None:
    project_root = tmp_path / "root"
    if exists:
        project_root.touch()
    with pytest.raises(AstcheckError, match="Could not render ASTcheck configuration"):
        YamlConfigurationRenderer().render(
            project_root=project_root,
            config_file=tmp_path / "astcheck.yaml",
            policy=analysis_policy,
        )
