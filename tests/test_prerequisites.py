import os
import stat
import tomllib
from collections.abc import Mapping
from pathlib import Path

import pytest

from checksmith.config import Check
from checksmith.dtos import CommandName, PackageType
from checksmith.errors import CheckPrerequisiteError
from checksmith.prerequisites import LocalProjectFiles, UvProjectPrerequisites


class RecordingProjectFiles:
    def __init__(
        self,
        document: Mapping[str, object],
        failure: tuple[str, Path] | None,
    ) -> None:
        self.document = document
        self.failure = failure
        self.calls: list[tuple[str, Path]] = []

    def _record(self, operation: str, path: Path) -> None:
        self.calls.append((operation, path))
        if self.failure == (operation, path):
            raise PermissionError("permission denied")

    def stat(self, *, path: Path, follow_symlinks: bool) -> os.stat_result:
        assert follow_symlinks
        self._record("stat", path)
        return os.stat_result((stat.S_IFREG, 0, 0, 0, 0, 0, 0, 0, 0, 0))

    def read_toml(self, *, path: Path) -> Mapping[str, object]:
        self._record("toml", path)
        return self.document

    def read_prefix(self, *, path: Path, size: int) -> bytes:
        assert size == 1
        self._record("prefix", path)
        return b"v"

    def resolve(self, *, path: Path) -> Path:
        raise AssertionError("UV validation must not resolve the project root")


@pytest.fixture
def uv_check() -> Check:
    return Check(
        id="unit-tests",
        package_type=PackageType.UV,
        package=None,
        command=CommandName.PYTEST,
        args=("tests",),
    )


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    (tmp_path / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    (tmp_path / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    return tmp_path


def test_uv_checks_only_the_required_files_in_order(uv_check: Check) -> None:
    root = Path("/project")
    files = RecordingProjectFiles(document={}, failure=None)

    UvProjectPrerequisites(environment={}, files=files).validate(
        check=uv_check, project_root=root
    )

    assert files.calls == [
        ("stat", root / "pyproject.toml"),
        ("toml", root / "pyproject.toml"),
        ("stat", root / "uv.lock"),
        ("prefix", root / "uv.lock"),
    ]


@pytest.mark.parametrize("variable", ["UV_PROJECT", "UV_WORKING_DIR"])
def test_uv_rejects_environment_redirects_before_reading_files(
    variable: str, uv_check: Check
) -> None:
    files = RecordingProjectFiles(document={}, failure=None)
    root = Path("/project")
    prerequisites = UvProjectPrerequisites(
        environment={variable: "/another-project"}, files=files
    )

    with pytest.raises(CheckPrerequisiteError, match=f"Unset {variable}") as raised:
        prerequisites.validate(check=uv_check, project_root=root)

    assert raised.value.check_id == uv_check.id
    assert raised.value.command is CommandName.PYTEST
    assert raised.value.config_file == root / "pyproject.toml"
    assert files.calls == []


@pytest.mark.parametrize("setting", ["", "1", "true", "TRUE", "yes", "invalid"])
def test_uv_rejects_disabled_sync_before_reading_files(
    setting: str, uv_check: Check
) -> None:
    files = RecordingProjectFiles(document={}, failure=None)
    prerequisites = UvProjectPrerequisites(
        environment={"UV_NO_SYNC": setting}, files=files
    )

    with pytest.raises(CheckPrerequisiteError, match="must be unset or false"):
        prerequisites.validate(check=uv_check, project_root=Path("/project"))

    assert files.calls == []


@pytest.mark.parametrize("setting", ["0", "false", "FALSE", "no", "off"])
def test_uv_accepts_explicitly_enabled_sync(setting: str, uv_check: Check) -> None:
    files = RecordingProjectFiles(document={}, failure=None)
    UvProjectPrerequisites(
        environment={"UV_NO_SYNC": setting, "UV_PROJECT": "", "UV_WORKING_DIR": ""},
        files=files,
    ).validate(check=uv_check, project_root=Path("/project"))

    assert len(files.calls) == 4


@pytest.mark.parametrize(
    ("operation", "filename", "call_count"),
    [
        ("stat", "pyproject.toml", 1),
        ("toml", "pyproject.toml", 2),
        ("stat", "uv.lock", 3),
        ("prefix", "uv.lock", 4),
    ],
)
def test_uv_file_failures_keep_context_and_stop_immediately(
    operation: str, filename: str, call_count: int, uv_check: Check
) -> None:
    root = Path("/project")
    failed_path = root / filename
    files = RecordingProjectFiles(document={}, failure=(operation, failed_path))

    with pytest.raises(CheckPrerequisiteError, match="permission denied") as raised:
        UvProjectPrerequisites(environment={}, files=files).validate(
            check=uv_check, project_root=root
        )

    assert raised.value.config_file == failed_path
    assert raised.value.check_id == uv_check.id
    assert len(files.calls) == call_count


def test_uv_rejects_unmanaged_projects_before_inspecting_the_lock(uv_check: Check) -> None:
    files = RecordingProjectFiles(
        document={"tool": {"uv": {"managed": False}}}, failure=None
    )
    with pytest.raises(CheckPrerequisiteError, match="tool.uv.managed=true"):
        UvProjectPrerequisites(environment={}, files=files).validate(
            check=uv_check, project_root=Path("/project")
        )

    assert [operation for operation, _ in files.calls] == ["stat", "toml"]


@pytest.mark.parametrize("filename", ["pyproject.toml", "uv.lock"])
@pytest.mark.parametrize("kind", ["absent", "directory", "broken-symlink"])
def test_uv_requires_regular_project_files(
    filename: str, kind: str, project_root: Path, uv_check: Check
) -> None:
    path = project_root / filename
    path.unlink()
    if kind == "directory":
        path.mkdir()
    elif kind == "broken-symlink":
        path.symlink_to(project_root / "missing")

    with pytest.raises(CheckPrerequisiteError, match="uv execution requires") as raised:
        UvProjectPrerequisites(environment={}, files=LocalProjectFiles()).validate(
            check=uv_check, project_root=project_root
        )

    assert raised.value.config_file == path


def test_uv_does_not_search_parent_directories(
    project_root: Path, uv_check: Check
) -> None:
    nested = project_root / "nested"
    nested.mkdir()

    with pytest.raises(CheckPrerequisiteError) as raised:
        UvProjectPrerequisites(environment={}, files=LocalProjectFiles()).validate(
            check=uv_check, project_root=nested
        )

    assert raised.value.config_file == nested / "pyproject.toml"


@pytest.mark.parametrize("content", [b"[project\n", b"\xff"])
def test_uv_reports_invalid_manifest_content(
    content: bytes, project_root: Path, uv_check: Check
) -> None:
    path = project_root / "pyproject.toml"
    path.write_bytes(content)

    with pytest.raises(CheckPrerequisiteError, match="invalid pyproject.toml") as raised:
        UvProjectPrerequisites(environment={}, files=LocalProjectFiles()).validate(
            check=uv_check, project_root=project_root
        )

    assert raised.value.config_file == path


def test_local_project_files_read_and_resolve_real_files(project_root: Path) -> None:
    files = LocalProjectFiles()
    manifest = project_root / "pyproject.toml"
    link = project_root / "manifest-link"
    link.symlink_to(manifest)

    assert files.read_toml(path=manifest) == {"project": {}}
    assert files.read_prefix(path=project_root / "uv.lock", size=3) == b"ver"
    assert stat.S_ISREG(files.stat(path=link, follow_symlinks=True).st_mode)
    assert stat.S_ISLNK(files.stat(path=link, follow_symlinks=False).st_mode)
    assert files.resolve(path=link) == manifest
    assert files.resolve(path=project_root / "absent") == project_root / "absent"


def test_local_project_files_preserve_missing_file_errors(tmp_path: Path) -> None:
    files = LocalProjectFiles()
    missing = tmp_path / "missing"

    with pytest.raises(FileNotFoundError):
        files.stat(path=missing, follow_symlinks=True)
    with pytest.raises(FileNotFoundError):
        files.read_toml(path=missing)
    with pytest.raises(FileNotFoundError):
        files.read_prefix(path=missing, size=1)


@pytest.mark.parametrize(
    ("content", "error"),
    [(b"[invalid", tomllib.TOMLDecodeError), (b"\xff", UnicodeDecodeError)],
)
def test_local_project_files_preserve_parser_errors(
    tmp_path: Path, content: bytes, error: type[ValueError]
) -> None:
    path = tmp_path / "pyproject.toml"
    path.write_bytes(content)

    with pytest.raises(error):
        LocalProjectFiles().read_toml(path=path)
