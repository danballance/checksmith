from collections.abc import Mapping
from pathlib import Path

import pytest
import yaml

from checksmith.adapters.driven.configuration.initialization import YamlConfigRenderer
from checksmith.domain.config import Config
from checksmith.domain.errors import ChecksmithError
from checksmith.domain.models import PackageType


def test_renderer_preserves_all_content_except_the_root_scalar(
    template: bytes,
    destination: Path,
) -> None:
    rendered = YamlConfigRenderer().render(
        content=template,
        config_file=destination / "checksmith.yaml",
        project_root=destination,
    )

    assert rendered.content == template.replace(
        b"project_root: ../../..", b'project_root: "."'
    )


def test_renderer_preserves_the_astcheck_git_requirement(
    template: bytes, destination: Path
) -> None:
    rendered = YamlConfigRenderer().render(
        content=template,
        config_file=destination / "checksmith.yaml",
        project_root=destination,
    )

    config = Config.from_mapping(
        document=yaml.safe_load(rendered.content),
        config_file=destination / "checksmith.yaml",
    )
    astcheck = next(check for check in config.checks if check.id == "astcheck")
    assert astcheck.package_type is PackageType.UVX
    assert astcheck.package == (
        "checksmith @ git+https://github.com/danballance/checksmith@main"
    )
    assert rendered.astcheck_package == astcheck.package


@pytest.mark.parametrize("checks", [b"checks: []", b"checks: {}", b"checks: [null]"])
def test_renderer_rejects_invalid_checks(destination: Path, checks: bytes) -> None:
    content = b"schema_version: 1\nproject_root: .\n" + checks

    with pytest.raises(ChecksmithError, match="checks"):
        YamlConfigRenderer().render(
            content=content,
            config_file=destination / "checksmith.yaml",
            project_root=destination,
        )


@pytest.mark.parametrize(
    "root_name",
    [
        "project space",
        "project: # comment",
        "quote'and\"double",
        "line\nbreak",
        "café",
        "project 🐍",
        "next\u0085line",
    ],
)
def test_renderer_safely_encodes_root_names_without_filesystem_access(
    template: bytes,
    destination: Path,
    root_name: str,
) -> None:
    project_root = destination / root_name
    config_file = destination / "checksmith.yaml"

    rendered = YamlConfigRenderer().render(
        content=template,
        config_file=config_file,
        project_root=project_root,
    )

    document: object = yaml.safe_load(rendered.content)
    assert isinstance(document, Mapping)
    assert document["project_root"] == root_name
    assert (
        Config.from_mapping(document=document, config_file=config_file).project_root
        == project_root
    )
    assert not tuple(destination.iterdir())


@pytest.mark.parametrize(
    "content",
    [b"", b"[]\n", b"checks: [\n", b"\xff\xfe", b"---\n{}\n---\n{}\n"],
)
def test_renderer_rejects_unreadable_or_malformed_yaml(
    destination: Path,
    content: bytes,
) -> None:
    with pytest.raises(ChecksmithError):
        YamlConfigRenderer().render(
            content=content,
            config_file=destination / "checksmith.yaml",
            project_root=destination,
        )


@pytest.mark.parametrize(
    "replacement",
    [
        b"",
        b"project_root: .\nproject_root: ..\n",
        b"project_root: [..]\n",
        b"project_root: {path: ..}\n",
        b"project_root: null\n",
        b"project_root: 42\n",
    ],
)
def test_renderer_rejects_invalid_template_roots(
    template: bytes,
    destination: Path,
    replacement: bytes,
) -> None:
    content = template.replace(b"project_root: ../../..\n", replacement)

    with pytest.raises(ChecksmithError, match="project_root"):
        YamlConfigRenderer().render(
            content=content,
            config_file=destination / "checksmith.yaml",
            project_root=destination,
        )


def test_renderer_rejects_schema_errors(
    template: bytes,
    destination: Path,
) -> None:
    content = template.replace(b"schema_version: 1", b"schema_version: 999")

    with pytest.raises(ChecksmithError, match="schema_version"):
        YamlConfigRenderer().render(
            content=content,
            config_file=destination / "checksmith.yaml",
            project_root=destination,
        )


def test_renderer_rejects_a_root_alias_without_rewriting_the_anchored_value(
    template: bytes,
    destination: Path,
) -> None:
    content = template.replace(
        b"project_root: ../../..\n",
        b"another_path: &root ../../..\nproject_root: *root\n",
    )

    with pytest.raises(ChecksmithError, match="project_root"):
        YamlConfigRenderer().render(
            content=content,
            config_file=destination / "checksmith.yaml",
            project_root=destination,
        )


@pytest.mark.parametrize("replacement", [b"command: ruff", b"command: astcheck"])
def test_renderer_requires_exactly_one_astcheck_command(
    template: bytes, destination: Path, replacement: bytes
) -> None:
    if replacement == b"command: ruff":
        template = template.replace(b"command: astcheck", replacement)
    else:
        template = template.replace(b"command: ruff", replacement)
    with pytest.raises(ChecksmithError, match="exactly one ASTcheck"):
        YamlConfigRenderer().render(
            content=template,
            config_file=destination / "checksmith.yaml",
            project_root=destination,
        )


def test_renderer_requires_astcheck_uvx_package(
    template: bytes, destination: Path
) -> None:
    template = template.replace(b"package_type: uvx", b"package_type: uv", 1)
    template = template.replace(
        b'package: "checksmith @ git+https://github.com/danballance/checksmith@main"',
        b"package: null",
        1,
    )
    with pytest.raises(ChecksmithError, match="uvx package requirement"):
        YamlConfigRenderer().render(
            content=template,
            config_file=destination / "checksmith.yaml",
            project_root=destination,
        )
