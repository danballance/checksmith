from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict


class PreparedFile(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: Path
    content: bytes


class PreparedConfiguration(BaseModel):
    model_config = ConfigDict(frozen=True)

    content: bytes
    astcheck_package: str


class AssetSource(Protocol):
    def read(self, *, name: str) -> bytes: ...


class ConfigRenderer(Protocol):
    def render(
        self, *, content: bytes, config_file: Path, project_root: Path
    ) -> PreparedConfiguration: ...


class InitializationFilesystem(Protocol):
    def is_directory(self, *, path: Path) -> bool: ...

    def entry_exists(self, *, path: Path) -> bool: ...

    def write_exclusive(self, *, path: Path, content: bytes) -> None: ...


class AstcheckConfigurator(Protocol):
    def configure(
        self,
        *,
        package: str,
        project_root: Path,
        config_file: Path,
        policy: Path | None,
    ) -> bytes: ...


class SetupCommandBuilder(Protocol):
    def build_argv(
        self,
        *,
        package: str | None,
        command: str,
        arguments: tuple[str, ...],
    ) -> tuple[str, ...]: ...
