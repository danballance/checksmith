import os
from pathlib import Path
from typing import ClassVar

from checksmith.application.ports.initialization import (
    AssetSource,
    AstcheckConfigurator,
    ConfigRenderer,
    InitializationFilesystem,
    PreparedFile,
)
from checksmith.domain.errors import ChecksmithError
from checksmith.domain.results import InitOutput


class Initializer:
    ASSET_NAMES: ClassVar[tuple[str, ...]] = (
        "checksmith.yaml",
        "ruff.toml",
        "semgrep.yaml",
        "coverage.toml",
    )
    GENERATED_NAMES: ClassVar[tuple[str, ...]] = ("astcheck.yaml",)

    def __init__(
        self,
        assets: AssetSource,
        renderer: ConfigRenderer,
        filesystem: InitializationFilesystem,
        astcheck: AstcheckConfigurator,
    ) -> None:
        self._assets = assets
        self._renderer = renderer
        self._filesystem = filesystem
        self._astcheck = astcheck

    def initialize(
        self,
        *,
        destination: Path,
        project_root: Path,
        astcheck_policy: Path | None,
    ) -> InitOutput:
        destination = Path(os.path.abspath(destination))
        project_root = Path(os.path.normpath(destination / project_root))
        self._require_directory(path=destination, label="Initialization destination")
        self._require_directory(path=project_root, label="Project root")

        destinations = tuple(
            destination / name for name in self.ASSET_NAMES + self.GENERATED_NAMES
        )
        collisions = tuple(
            path for path in destinations if self._entry_exists(path=path)
        )
        if collisions:
            raise ChecksmithError(
                "Refusing to overwrite existing paths: "
                + ", ".join(str(path) for path in collisions)
            )

        configuration = self._renderer.render(
            content=self._read_asset(name="checksmith.yaml"),
            config_file=destinations[0],
            project_root=project_root,
        )
        assets = tuple(
            PreparedFile(path=path, content=self._read_asset(name=path.name))
            for path in destinations[1:-1]
        )
        policy = (
            Path(os.path.abspath(astcheck_policy))
            if astcheck_policy is not None
            else None
        )
        generated = self._astcheck.configure(
            package=configuration.astcheck_package,
            project_root=project_root,
            config_file=destinations[-1],
            policy=policy,
        )
        prepared = (
            PreparedFile(path=destinations[0], content=configuration.content),
            *assets,
            PreparedFile(path=destinations[-1], content=generated),
        )
        for file in prepared:
            try:
                self._filesystem.write_exclusive(path=file.path, content=file.content)
            except OSError as error:
                raise ChecksmithError(
                    f"Could not create {file.path}: {error}"
                ) from error

        return InitOutput(project_root=project_root, created_files=destinations)

    def _require_directory(self, *, path: Path, label: str) -> None:
        try:
            is_directory = self._filesystem.is_directory(path=path)
        except OSError as error:
            raise ChecksmithError(f"Could not inspect {path}: {error}") from error
        if not is_directory:
            raise ChecksmithError(f"{label} is not an existing directory: {path}")

    def _entry_exists(self, *, path: Path) -> bool:
        try:
            return self._filesystem.entry_exists(path=path)
        except OSError as error:
            raise ChecksmithError(f"Could not inspect {path}: {error}") from error

    def _read_asset(self, *, name: str) -> bytes:
        try:
            return self._assets.read(name=name)
        except OSError as error:
            raise ChecksmithError(
                f"Could not read bundled asset {name}: {error}"
            ) from error
