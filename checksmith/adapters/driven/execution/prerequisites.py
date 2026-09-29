import os
import tomllib
from collections.abc import Mapping
from pathlib import Path
from stat import S_ISREG
from typing import Protocol

from checksmith.domain.config import Check
from checksmith.domain.errors import CheckPrerequisiteError


class ProjectFiles(Protocol):
    def stat(self, *, path: Path, follow_symlinks: bool) -> os.stat_result: ...

    def read_toml(self, *, path: Path) -> Mapping[str, object]: ...

    def read_prefix(self, *, path: Path, size: int) -> bytes: ...

    def resolve(self, *, path: Path) -> Path: ...


class LocalProjectFiles:
    def stat(self, *, path: Path, follow_symlinks: bool) -> os.stat_result:
        return path.stat(follow_symlinks=follow_symlinks)

    def read_toml(self, *, path: Path) -> Mapping[str, object]:
        with path.open("rb") as stream:
            return tomllib.load(stream)

    def read_prefix(self, *, path: Path, size: int) -> bytes:
        with path.open("rb") as stream:
            return stream.read(size)

    def resolve(self, *, path: Path) -> Path:
        return path.resolve(strict=False)


class UvPrerequisites(Protocol):
    def validate(self, *, check: Check, project_root: Path) -> None: ...


class UvProjectPrerequisites:
    def __init__(self, environment: Mapping[str, str], files: ProjectFiles) -> None:
        self._environment = environment
        self._files = files

    def validate(self, *, check: Check, project_root: Path) -> None:
        for variable in ("UV_PROJECT", "UV_WORKING_DIR"):
            if self._environment.get(variable):
                raise CheckPrerequisiteError(
                    check_id=check.id,
                    command=check.command,
                    config_file=project_root / "pyproject.toml",
                    problem=(
                        f"Unset {variable}; Checksmith uses project_root "
                        "to select the uv project and working directory"
                    ),
                )
        no_sync = self._environment.get("UV_NO_SYNC")
        if no_sync is not None and no_sync.lower() not in {"0", "false", "no", "off"}:
            raise CheckPrerequisiteError(
                check_id=check.id,
                command=check.command,
                config_file=project_root / "pyproject.toml",
                problem="UV_NO_SYNC must be unset or false for locked uv execution",
            )
        for filename in ("pyproject.toml", "uv.lock"):
            config_file = project_root / filename
            try:
                if not S_ISREG(
                    self._files.stat(path=config_file, follow_symlinks=True).st_mode
                ):
                    raise OSError(f"Expected a regular file: {config_file}")
                if filename == "pyproject.toml":
                    try:
                        document = self._files.read_toml(path=config_file)
                    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as error:
                        raise ValueError(f"invalid pyproject.toml: {error}") from error
                    tool = document.get("tool")
                    uv = tool.get("uv") if isinstance(tool, Mapping) else None
                    if isinstance(uv, Mapping) and uv.get("managed") is False:
                        raise ValueError("uv execution requires tool.uv.managed=true")
                else:
                    self._files.read_prefix(path=config_file, size=1)
            except (OSError, ValueError) as error:
                raise CheckPrerequisiteError(
                    check_id=check.id,
                    command=check.command,
                    config_file=config_file,
                    problem=(
                        "uv execution requires readable pyproject.toml and uv.lock "
                        f"files in the project root: {error}"
                    ),
                ) from error
