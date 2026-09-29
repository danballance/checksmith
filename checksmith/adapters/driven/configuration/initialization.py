import os
from collections.abc import Mapping
from pathlib import Path

import yaml

from checksmith.application.ports.initialization import PreparedConfiguration
from checksmith.domain.config import Config
from checksmith.domain.errors import ChecksmithError
from checksmith.domain.models import CommandName, PackageType


class YamlConfigRenderer:
    def render(
        self,
        *,
        content: bytes,
        config_file: Path,
        project_root: Path,
    ) -> PreparedConfiguration:
        try:
            text = content.decode("utf-8")
            document = yaml.compose(text, Loader=yaml.SafeLoader)
        except (UnicodeDecodeError, yaml.YAMLError) as error:
            raise ChecksmithError(
                f"Invalid bundled checksmith.yaml: {error}"
            ) from error

        if not isinstance(document, yaml.MappingNode):
            raise ChecksmithError(
                "Bundled checksmith.yaml must contain a YAML mapping."
            )
        roots = tuple(
            (key, value)
            for key, value in document.value
            if isinstance(key, yaml.ScalarNode) and key.value == "project_root"
        )
        if len(roots) != 1:
            raise ChecksmithError(
                "Bundled checksmith.yaml must declare exactly one project_root."
            )
        key, root = roots[0]
        if (
            not isinstance(root, yaml.ScalarNode)
            or root.tag != "tag:yaml.org,2002:str"
            or root.start_mark.index < key.end_mark.index
        ):
            raise ChecksmithError(
                "Bundled checksmith.yaml project_root must be a string, not an alias."
            )

        relative_root = os.path.relpath(project_root, config_file.parent)
        rendered = (
            text[: root.start_mark.index]
            + yaml.safe_dump(relative_root, default_style='"').rstrip("\n")
            + text[root.end_mark.index :]
        )
        try:
            generated: object = yaml.safe_load(rendered)
        except yaml.YAMLError as error:
            raise ChecksmithError(
                f"Invalid bundled checksmith.yaml: {error}"
            ) from error
        if not isinstance(generated, Mapping):
            raise ChecksmithError(
                "Generated checksmith.yaml must contain a YAML mapping."
            )
        configuration = Config.from_mapping(document=generated, config_file=config_file)
        checks = tuple(
            check
            for check in configuration.checks
            if check.command is CommandName.ASTCHECK
        )
        if len(checks) != 1:
            raise ChecksmithError(
                "Bundled checksmith.yaml must declare exactly one ASTcheck command."
            )
        check = checks[0]
        if check.package_type is not PackageType.UVX or check.package is None:
            raise ChecksmithError(
                "Bundled ASTcheck command must use a uvx package requirement."
            )
        return PreparedConfiguration(
            content=rendered.encode("utf-8"), astcheck_package=check.package
        )
