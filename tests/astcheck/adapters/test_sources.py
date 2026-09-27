import os
from pathlib import Path

import pytest

from astcheck.adapters.sources import LocalSourceRepository
from astcheck.domain.errors import AstcheckError


def test_discovery_is_recursive_sorted_and_deduplicated(tmp_path: Path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    nested = source / "package"
    nested.mkdir()
    first = source / "a.py"
    second = nested / "z.py"
    first.write_text("value = 1\n", encoding="utf-8")
    second.write_text("value = 2\n", encoding="utf-8")
    (source / "data.json").write_text("{}", encoding="utf-8")
    (source / "contract.pyi").write_text("value: int", encoding="utf-8")

    paths = LocalSourceRepository().discover(
        root=tmp_path, sources=("src/package", "src", "src/a.py"), exclusions=()
    )

    assert paths == (first, second)


@pytest.mark.parametrize("pattern", ["src/generated", "src/generated/**", "**/generated/**"])
def test_directory_exclusions_prune_subtrees_and_symlinks(
    tmp_path: Path, pattern: str
) -> None:
    source = tmp_path / "src"
    generated = source / "generated"
    generated.mkdir(parents=True)
    included = source / "app.py"
    included.write_text("value = 1", encoding="utf-8")
    (generated / "module.py").write_text("value = 2", encoding="utf-8")
    (generated / "loop").symlink_to(source, target_is_directory=True)

    paths = LocalSourceRepository().discover(
        root=tmp_path, sources=("src",), exclusions=(pattern,)
    )

    assert paths == (included,)


def test_file_exclusions_apply_to_explicit_files(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("value = 1", encoding="utf-8")
    (tmp_path / "test_app.py").write_text("value = 2", encoding="utf-8")

    paths = LocalSourceRepository().discover(
        root=tmp_path,
        sources=("test_app.py", "app.py"),
        exclusions=("test_*.py",),
    )

    assert paths == (tmp_path / "app.py",)


@pytest.mark.parametrize("kind", ["empty-directory", "non-python", "excluded"])
def test_empty_source_selection_is_an_error(tmp_path: Path, kind: str) -> None:
    exclusions: tuple[str, ...] = ()
    if kind == "non-python":
        (tmp_path / "data.json").write_text("{}", encoding="utf-8")
    elif kind == "excluded":
        (tmp_path / "app.py").write_text("value = 1", encoding="utf-8")
        exclusions = ("*.py",)

    with pytest.raises(AstcheckError, match="no Python files"):
        LocalSourceRepository().discover(
            root=tmp_path, sources=(".",), exclusions=exclusions
        )


def test_missing_source_fails_before_analysis(tmp_path: Path) -> None:
    with pytest.raises(AstcheckError, match="source does not exist"):
        LocalSourceRepository().discover(
            root=tmp_path, sources=("missing.py",), exclusions=()
        )


def test_unexcluded_symlink_is_an_error(tmp_path: Path) -> None:
    (tmp_path / "source.py").write_text("value = 1", encoding="utf-8")
    (tmp_path / "link.py").symlink_to(tmp_path / "source.py")

    with pytest.raises(AstcheckError, match="symbolic links must be explicitly excluded"):
        LocalSourceRepository().discover(root=tmp_path, sources=(".",), exclusions=())


@pytest.mark.parametrize("external", [False, True])
@pytest.mark.parametrize("selection", ["linked/file.py", "linked/subdir"])
def test_explicit_paths_cannot_traverse_intermediate_symlinks(
    tmp_path: Path, external: bool, selection: str
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    target = (tmp_path if external else root) / "target"
    (target / "subdir").mkdir(parents=True)
    (target / "file.py").write_text("value = 1", encoding="utf-8")
    (target / "subdir" / "module.py").write_text("value = 2", encoding="utf-8")
    (root / "linked").symlink_to(target, target_is_directory=True)

    with pytest.raises(AstcheckError, match="symbolic links must be explicitly excluded"):
        LocalSourceRepository().discover(
            root=root, sources=(selection,), exclusions=()
        )


@pytest.mark.parametrize("selection", ["linked/file.py", "linked/subdir"])
@pytest.mark.parametrize("pattern", ["linked", "linked/**", "**/linked/**"])
def test_excluded_explicit_subpaths_do_not_follow_intermediate_symlinks(
    tmp_path: Path, selection: str, pattern: str
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    target = tmp_path / "outside"
    (target / "subdir").mkdir(parents=True)
    (target / "file.py").write_text("value = 1", encoding="utf-8")
    (root / "linked").symlink_to(target, target_is_directory=True)
    included = root / "app.py"
    included.write_text("value = 2", encoding="utf-8")

    paths = LocalSourceRepository().discover(
        root=root, sources=(selection, "app.py"), exclusions=(pattern,)
    )

    assert paths == (included,)


def test_nonregular_sources_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "pipe.py"
    os.mkfifo(path)

    with pytest.raises(AstcheckError, match="not a regular file or directory"):
        LocalSourceRepository().discover(
            root=tmp_path, sources=("pipe.py",), exclusions=()
        )


def test_directory_access_errors_are_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def inaccessible(path: Path) -> bool:
        raise PermissionError("directory access denied")

    monkeypatch.setattr(Path, "is_dir", inaccessible)

    with pytest.raises(AstcheckError, match="directory access denied"):
        LocalSourceRepository().discover(root=tmp_path, sources=(".",), exclusions=())


def test_read_honors_python_source_encoding(tmp_path: Path) -> None:
    path = tmp_path / "encoded.py"
    path.write_bytes(b"# coding: latin-1\nword = 'caf\xe9'\n")

    assert LocalSourceRepository().read(path=path) == (
        "# coding: latin-1\nword = 'caf\xe9'\n"
    )


@pytest.mark.parametrize(
    "content", [b"\xff", b"# coding: missing-encoding\nvalue = 1", b"# coding: ascii\n\xff"]
)
def test_invalid_source_encoding_is_an_analysis_error(
    tmp_path: Path, content: bytes
) -> None:
    path = tmp_path / "invalid.py"
    path.write_bytes(content)

    with pytest.raises(AstcheckError, match="Could not read source"):
        LocalSourceRepository().read(path=path)


def test_missing_source_cannot_be_read(tmp_path: Path) -> None:
    with pytest.raises(AstcheckError, match="Could not read source"):
        LocalSourceRepository().read(path=tmp_path / "missing.py")
