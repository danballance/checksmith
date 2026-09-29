from pathlib import Path
from typing import Protocol

from astcheck.domain.configuration import AnalysisPolicy


class PolicyLoader(Protocol):
    def load(self, *, path: Path) -> AnalysisPolicy: ...


class PolicyPrompts(Protocol):
    def prompt(self) -> AnalysisPolicy: ...


class PolicyValidator(Protocol):
    def validate_policy(self, *, policy: AnalysisPolicy) -> None: ...


class ConfigurationRenderer(Protocol):
    def render(
        self, *, project_root: Path, config_file: Path, policy: AnalysisPolicy
    ) -> str: ...
