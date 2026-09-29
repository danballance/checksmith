from importlib.resources import files
from pathlib import Path

import pytest

from checksmith.adapters.driven.filesystem.initialization import (
    LocalInitializationFilesystem,
    PackagedAssetSource,
)


def test_asset_source_reads_the_injected_directory_exactly(tmp_path: Path) -> None:
    content = b"\x00\xffarbitrary asset bytes\r\n"
    (tmp_path / "fixture.bin").write_bytes(content)

    assert PackagedAssetSource(directory=tmp_path).read(name="fixture.bin") == content


def test_asset_source_reads_packaged_resources(template: bytes) -> None:
    source = PackagedAssetSource(directory=files("checksmith") / "assets" / "default")

    assert source.read(name="checksmith.yaml") == template


@pytest.mark.parametrize("asset_kind", ["missing", "directory"])
def test_asset_source_propagates_read_errors(tmp_path: Path, asset_kind: str) -> None:
    if asset_kind == "directory":
        (tmp_path / "asset").mkdir()
    source = PackagedAssetSource(directory=tmp_path)

    with pytest.raises(OSError):
        source.read(name="asset")


@pytest.mark.parametrize("entry_kind", ["missing", "file", "directory", "symlink"])
def test_local_filesystem_checks_directories(tmp_path: Path, entry_kind: str) -> None:
    path = tmp_path / "entry"
    if entry_kind == "file":
        path.write_bytes(b"file content")
    elif entry_kind == "directory":
        path.mkdir()
    elif entry_kind == "symlink":
        path.symlink_to(tmp_path, target_is_directory=True)

    assert LocalInitializationFilesystem().is_directory(path=path) is (
        entry_kind in {"directory", "symlink"}
    )


@pytest.mark.parametrize(
    "entry_kind", ["missing", "file", "directory", "dangling symlink"]
)
def test_local_filesystem_detects_entries(tmp_path: Path, entry_kind: str) -> None:
    path = tmp_path / "entry"
    if entry_kind == "file":
        path.write_bytes(b"file content")
    elif entry_kind == "directory":
        path.mkdir()
    elif entry_kind == "dangling symlink":
        path.symlink_to(tmp_path / "missing-target")

    assert LocalInitializationFilesystem().entry_exists(path=path) is (
        entry_kind != "missing"
    )


def test_local_filesystem_writes_bytes_exclusively(tmp_path: Path) -> None:
    path = tmp_path / "created"
    filesystem = LocalInitializationFilesystem()
    filesystem.write_exclusive(path=path, content=b"original\x00\xff\r\n")

    assert path.read_bytes() == b"original\x00\xff\r\n"
    with pytest.raises(FileExistsError):
        filesystem.write_exclusive(path=path, content=b"replacement")
    assert path.read_bytes() == b"original\x00\xff\r\n"


def test_local_filesystem_propagates_write_errors(tmp_path: Path) -> None:
    path = tmp_path / "missing directory" / "file"

    with pytest.raises(FileNotFoundError):
        LocalInitializationFilesystem().write_exclusive(path=path, content=b"content")
    assert not path.exists()
