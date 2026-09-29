import os
from pathlib import Path

import yaml
from pydantic import ValidationError

from astcheck.domain.configuration import AnalysisConfiguration, AnalysisPolicy
from astcheck.domain.errors import AstcheckError


class YamlConfigurationLoader:
    def load(self, *, path: Path) -> AnalysisConfiguration:
        try:
            document: object = yaml.safe_load(path.read_text(encoding="utf-8"))
            configuration = AnalysisConfiguration.model_validate(document)
            root = (path.absolute().parent / configuration.project_root).resolve(
                strict=True
            )
            if not root.is_dir():
                raise ValueError(f"project_root must be a directory: {root}")
        except (
            OSError,
            UnicodeError,
            yaml.YAMLError,
            ValidationError,
            ValueError,
        ) as error:
            raise AstcheckError(
                f"Could not load configuration {path}: {error}", None
            ) from error
        return configuration.model_copy(update={"project_root": root})


class YamlPolicyLoader:
    def load(self, *, path: Path) -> AnalysisPolicy:
        try:
            document: object = yaml.safe_load(path.read_text(encoding="utf-8"))
            return AnalysisPolicy.model_validate(document)
        except (OSError, UnicodeError, yaml.YAMLError) as error:
            raise AstcheckError(
                f"Could not read ASTcheck policy {path}: {error}", None
            ) from error
        except (TypeError, ValueError) as error:
            raise AstcheckError(
                f"Invalid ASTcheck policy {path}: {error}", None
            ) from error


class YamlConfigurationRenderer:
    def render(
        self, *, project_root: Path, config_file: Path, policy: AnalysisPolicy
    ) -> str:
        try:
            root = project_root.resolve(strict=True)
            if not root.is_dir():
                raise ValueError(f"project_root must be a directory: {root}")
            configuration = AnalysisConfiguration(
                schema_version=1,
                project_root=Path(os.path.relpath(project_root, config_file.parent)),
                sources=policy.sources,
                exclusions=policy.exclusions,
                plugins=policy.plugins,
            )
            return yaml.safe_dump(
                configuration.model_dump(mode="json"), sort_keys=False
            )
        except (OSError, ValueError, yaml.YAMLError) as error:
            raise AstcheckError(
                f"Could not render ASTcheck configuration: {error}", None
            ) from error
