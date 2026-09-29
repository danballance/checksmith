from pathlib import Path
from typing import Protocol

from checksmith.domain.config import Config


class ConfigurationLoader(Protocol):
    def load(self, *, config_file: Path, working_directory: Path) -> Config: ...
