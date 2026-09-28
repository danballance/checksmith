import os
from collections.abc import Mapping
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import ClassVar, Protocol

import yaml
from pydantic import BaseModel, ConfigDict

from astcheck.domain.configuration import AnalysisConfiguration, AnalysisPolicy
from checksmith.config import Config
from checksmith.errors import ChecksmithError
from checksmith.outputs.initoutput import InitOutput


class AssetSource(Protocol):
    def read(self, *, name: str) -> bytes: ...


class ConfigRenderer(Protocol):
    def render(
        self,
        *,
        content: bytes,
        config_file: Path,
        project_root: Path,
    ) -> bytes: ...


class InitializationFilesystem(Protocol):
    def is_directory(self, *, path: Path) -> bool: ...

    def entry_exists(self, *, path: Path) -> bool: ...

    def write_exclusive(self, *, path: Path, content: bytes) -> None: ...


class PreparedFile(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: Path
    content: bytes


class PackagedAssetSource:
    def __init__(self, directory: Traversable) -> None:
        self._directory = directory

    def read(self, *, name: str) -> bytes:
        return (self._directory / name).read_bytes()


class YamlConfigRenderer:
    def render(
        self,
        *,
        content: bytes,
        config_file: Path,
        project_root: Path,
    ) -> bytes:
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
        Config.from_mapping(document=generated, config_file=config_file)
        return rendered.encode("utf-8")


class LocalInitializationFilesystem:
    def is_directory(self, *, path: Path) -> bool:
        return path.is_dir()

    def entry_exists(self, *, path: Path) -> bool:
        return path.exists() or path.is_symlink()

    def write_exclusive(self, *, path: Path, content: bytes) -> None:
        with path.open("xb") as stream:
            stream.write(content)


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
    ) -> None:
        self._assets = assets
        self._renderer = renderer
        self._filesystem = filesystem

    def initialize(
        self,
        *,
        destination: Path,
        project_root: Path,
        astcheck_policy: AnalysisPolicy,
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

        prepared = tuple(
            self._prepare_file(
                path=path, project_root=project_root, astcheck_policy=astcheck_policy
            )
            for path in destinations
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

    def _prepare_file(
        self,
        *,
        path: Path,
        project_root: Path,
        astcheck_policy: AnalysisPolicy,
    ) -> PreparedFile:
        if path.name == "astcheck.yaml":
            configuration = AnalysisConfiguration(
                schema_version=1,
                project_root=Path(os.path.relpath(project_root, path.parent)),
                sources=astcheck_policy.sources,
                exclusions=astcheck_policy.exclusions,
                plugins=astcheck_policy.plugins,
            )
            return PreparedFile(
                path=path,
                content=yaml.safe_dump(
                    configuration.model_dump(mode="json"), sort_keys=False
                ).encode("utf-8"),
            )
        try:
            content = self._assets.read(name=path.name)
        except OSError as error:
            raise ChecksmithError(
                f"Could not read bundled asset {path.name}: {error}"
            ) from error
        if path.name == "checksmith.yaml":
            content = self._renderer.render(
                content=content,
                config_file=path,
                project_root=project_root,
            )
        return PreparedFile(path=path, content=content)
