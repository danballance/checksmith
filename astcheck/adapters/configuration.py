from pathlib import Path

import yaml
from pydantic import ValidationError

from astcheck.domain.configuration import AnalysisConfiguration
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
        except (OSError, UnicodeError, yaml.YAMLError, ValidationError, ValueError) as error:
            raise AstcheckError(
                f"Could not load configuration {path}: {error}", None
            ) from error
        return configuration.model_copy(update={"project_root": root})
