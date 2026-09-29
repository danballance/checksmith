from importlib.resources.abc import Traversable
from pathlib import Path


class PackagedAssetSource:
    def __init__(self, directory: Traversable) -> None:
        self._directory = directory

    def read(self, *, name: str) -> bytes:
        return (self._directory / name).read_bytes()


class LocalInitializationFilesystem:
    def is_directory(self, *, path: Path) -> bool:
        return path.is_dir()

    def entry_exists(self, *, path: Path) -> bool:
        return path.exists() or path.is_symlink()

    def write_exclusive(self, *, path: Path, content: bytes) -> None:
        with path.open("xb") as stream:
            stream.write(content)
