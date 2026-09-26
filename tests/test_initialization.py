from collections.abc import Mapping
from importlib.resources import files
from pathlib import Path

import pytest
import yaml
from pydantic import BaseModel, ValidationError

from checksmith.config import Config, ConfigPath
from checksmith.errors import ChecksmithError
from checksmith.initialization import (
    Initializer,
    LocalInitializationFilesystem,
    PackagedAssetSource,
    PreparedFile,
    YamlConfigRenderer,
)

ASSET_NAMES = (
    "checksmith.yaml",
    "ruff.toml",
    "semgrep.yaml",
    "coverage.toml",
)


class RenderCall(BaseModel):
    content: bytes
    config_file: Path
    project_root: Path


class RecordingAssetSource:
    def __init__(
        self,
        contents: Mapping[str, bytes],
        events: list[str],
        failed_name: str | None,
    ) -> None:
        self.contents = contents
        self.events = events
        self.failed_name = failed_name

    def read(self, *, name: str) -> bytes:
        self.events.append(f"read:{name}")
        if name == self.failed_name:
            raise OSError("asset unavailable")
        return self.contents[name]


class RecordingConfigRenderer:
    def __init__(
        self,
        rendered: bytes,
        events: list[str],
        error: ChecksmithError | None,
    ) -> None:
        self.rendered = rendered
        self.events = events
        self.error = error
        self.calls: list[RenderCall] = []

    def render(
        self, *, content: bytes, config_file: Path, project_root: Path
    ) -> bytes:
        self.events.append(f"render:{config_file.name}")
        self.calls.append(
            RenderCall(
                content=content,
                config_file=config_file,
                project_root=project_root,
            )
        )
        if self.error is not None:
            raise self.error
        return self.rendered


class RecordingFilesystem:
    def __init__(
        self,
        directories: set[Path],
        entries: set[Path],
        events: list[str],
        refused: Path | None,
        unreadable: Path | None,
    ) -> None:
        self.directories = directories
        self.entries = entries
        self.events = events
        self.refused = refused
        self.unreadable = unreadable
        self.written: list[PreparedFile] = []

    def is_directory(self, *, path: Path) -> bool:
        self.events.append(f"directory:{path}")
        if path == self.unreadable:
            raise PermissionError("inspection denied")
        return path in self.directories

    def entry_exists(self, *, path: Path) -> bool:
        self.events.append(f"exists:{path.name}")
        if path == self.unreadable:
            raise PermissionError("inspection denied")
        return path in self.entries

    def write_exclusive(self, *, path: Path, content: bytes) -> None:
        self.events.append(f"write:{path.name}")
        if path == self.refused:
            raise PermissionError("creation denied")
        self.written.append(PreparedFile(path=path, content=content))


class CompetingFilesystem:
    def __init__(
        self, filesystem: LocalInitializationFilesystem, raced: Path
    ) -> None:
        self.filesystem = filesystem
        self.raced = raced

    def is_directory(self, *, path: Path) -> bool:
        return self.filesystem.is_directory(path=path)

    def entry_exists(self, *, path: Path) -> bool:
        return self.filesystem.entry_exists(path=path)

    def write_exclusive(self, *, path: Path, content: bytes) -> None:
        if path == self.raced:
            path.write_bytes(b"created by another process")
        self.filesystem.write_exclusive(path=path, content=content)


@pytest.fixture
def destination(tmp_path: Path) -> Path:
    directory = tmp_path / "configuration"
    directory.mkdir()
    return directory


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    directory = tmp_path / "project"
    directory.mkdir()
    return directory


@pytest.fixture
def template() -> bytes:
    return (files("checksmith") / "assets" / "default" / "checksmith.yaml").read_bytes()


@pytest.fixture
def asset_directory(tmp_path: Path) -> Path:
    directory = tmp_path / "assets"
    directory.mkdir()
    packaged = files("checksmith") / "assets" / "default"
    for name in ASSET_NAMES:
        (directory / name).write_bytes((packaged / name).read_bytes())
    return directory


@pytest.fixture
def initializer(asset_directory: Path) -> Initializer:
    return Initializer(
        assets=PackagedAssetSource(directory=asset_directory),
        renderer=YamlConfigRenderer(),
        filesystem=LocalInitializationFilesystem(),
    )


@pytest.fixture
def events() -> list[str]:
    return []


@pytest.fixture
def asset_source(events: list[str]) -> RecordingAssetSource:
    return RecordingAssetSource(
        contents={name: f"original {name}".encode() for name in ASSET_NAMES},
        events=events,
        failed_name=None,
    )


@pytest.fixture
def renderer(events: list[str]) -> RecordingConfigRenderer:
    return RecordingConfigRenderer(rendered=b"rendered YAML", events=events, error=None)


@pytest.fixture
def filesystem(
    destination: Path, project_root: Path, events: list[str]
) -> RecordingFilesystem:
    return RecordingFilesystem(
        directories={destination, project_root},
        entries=set(),
        events=events,
        refused=None,
        unreadable=None,
    )


@pytest.fixture
def orchestrator(
    asset_source: RecordingAssetSource,
    renderer: RecordingConfigRenderer,
    filesystem: RecordingFilesystem,
) -> Initializer:
    return Initializer(assets=asset_source, renderer=renderer, filesystem=filesystem)


def test_orchestration_prepares_every_asset_before_writing(
    orchestrator: Initializer,
    asset_source: RecordingAssetSource,
    renderer: RecordingConfigRenderer,
    filesystem: RecordingFilesystem,
    events: list[str],
    destination: Path,
    project_root: Path,
) -> None:
    output = orchestrator.initialize(destination=destination, project_root=project_root)

    assert Initializer.ASSET_NAMES == ASSET_NAMES
    assert output.project_root == project_root
    assert output.created_files == tuple(destination / name for name in ASSET_NAMES)
    assert renderer.calls == [
        RenderCall(
            content=asset_source.contents["checksmith.yaml"],
            config_file=destination / "checksmith.yaml",
            project_root=project_root,
        )
    ]
    assert filesystem.written == [
        PreparedFile(
            path=destination / name,
            content=(
                renderer.rendered
                if name == "checksmith.yaml"
                else asset_source.contents[name]
            ),
        )
        for name in ASSET_NAMES
    ]
    assert events == [
        f"directory:{destination}",
        f"directory:{project_root}",
        *(f"exists:{name}" for name in ASSET_NAMES),
        "read:checksmith.yaml",
        "render:checksmith.yaml",
        *(f"read:{name}" for name in ASSET_NAMES[1:]),
        *(f"write:{name}" for name in ASSET_NAMES),
    ]


@pytest.mark.parametrize("name", ASSET_NAMES)
def test_asset_read_failures_stop_preparation_without_writing(
    orchestrator: Initializer,
    asset_source: RecordingAssetSource,
    filesystem: RecordingFilesystem,
    events: list[str],
    destination: Path,
    project_root: Path,
    name: str,
) -> None:
    asset_source.failed_name = name

    with pytest.raises(ChecksmithError, match=f"Could not read bundled asset {name}"):
        orchestrator.initialize(destination=destination, project_root=project_root)

    assert events[-1] == f"read:{name}"
    assert not filesystem.written


def test_render_failure_stops_preparation_without_writing(
    orchestrator: Initializer,
    renderer: RecordingConfigRenderer,
    filesystem: RecordingFilesystem,
    events: list[str],
    destination: Path,
    project_root: Path,
) -> None:
    error = ChecksmithError("invalid template")
    renderer.error = error

    with pytest.raises(ChecksmithError) as raised:
        orchestrator.initialize(destination=destination, project_root=project_root)

    assert raised.value is error
    assert events[-1] == "render:checksmith.yaml"
    assert not filesystem.written


@pytest.mark.parametrize("invalid_directory", ["destination", "project_root"])
def test_invalid_directories_fail_before_reading_assets(
    orchestrator: Initializer,
    filesystem: RecordingFilesystem,
    events: list[str],
    destination: Path,
    project_root: Path,
    invalid_directory: str,
) -> None:
    invalid_path = destination if invalid_directory == "destination" else project_root
    filesystem.directories.remove(invalid_path)

    with pytest.raises(ChecksmithError) as raised:
        orchestrator.initialize(destination=destination, project_root=project_root)

    assert str(invalid_path) in str(raised.value)
    expected_events = [f"directory:{destination}"]
    if invalid_directory == "project_root":
        expected_events.append(f"directory:{project_root}")
    assert events == expected_events
    assert not filesystem.written


@pytest.mark.parametrize("path_kind", ["destination", "project_root", "asset"])
def test_inspection_errors_have_path_context_and_stop_initialization(
    orchestrator: Initializer,
    filesystem: RecordingFilesystem,
    events: list[str],
    destination: Path,
    project_root: Path,
    path_kind: str,
) -> None:
    paths = {
        "destination": destination,
        "project_root": project_root,
        "asset": destination / "ruff.toml",
    }
    filesystem.unreadable = paths[path_kind]

    with pytest.raises(ChecksmithError) as raised:
        orchestrator.initialize(destination=destination, project_root=project_root)

    assert str(filesystem.unreadable) in str(raised.value)
    assert "Could not inspect" in str(raised.value)
    assert "inspection denied" in str(raised.value)
    assert isinstance(raised.value.__cause__, PermissionError)
    assert events[-1] == (
        "exists:ruff.toml" if path_kind == "asset" else f"directory:{paths[path_kind]}"
    )
    assert not filesystem.written


def test_preflight_reports_all_collisions_before_reading_assets(
    orchestrator: Initializer,
    filesystem: RecordingFilesystem,
    events: list[str],
    destination: Path,
    project_root: Path,
) -> None:
    filesystem.entries = {destination / "ruff.toml", destination / "coverage.toml"}

    with pytest.raises(ChecksmithError) as raised:
        orchestrator.initialize(destination=destination, project_root=project_root)

    assert all(str(path) in str(raised.value) for path in filesystem.entries)
    assert events == [
        f"directory:{destination}",
        f"directory:{project_root}",
        *(f"exists:{name}" for name in ASSET_NAMES),
    ]
    assert not filesystem.written


@pytest.mark.parametrize("name", ASSET_NAMES)
def test_a_write_failure_keeps_earlier_files_and_stops(
    orchestrator: Initializer,
    filesystem: RecordingFilesystem,
    events: list[str],
    destination: Path,
    project_root: Path,
    name: str,
) -> None:
    filesystem.refused = destination / name

    with pytest.raises(ChecksmithError) as raised:
        orchestrator.initialize(destination=destination, project_root=project_root)

    assert str(filesystem.refused) in str(raised.value)
    assert "creation denied" in str(raised.value)
    assert isinstance(raised.value.__cause__, PermissionError)
    assert events[-1] == f"write:{name}"
    assert [written.path.name for written in filesystem.written] == list(
        ASSET_NAMES[:ASSET_NAMES.index(name)]
    )


def test_initialization_writes_exactly_the_four_starter_files(
    initializer: Initializer,
    destination: Path,
) -> None:
    output = initializer.initialize(destination=destination, project_root=destination)

    expected_files = tuple(destination / name for name in ASSET_NAMES)
    assert output.project_root == destination
    assert output.created_files == expected_files
    assert set(destination.iterdir()) == set(expected_files)
    assert yaml.safe_load((destination / "checksmith.yaml").read_bytes())[
        "project_root"
    ] == "."


def test_supporting_files_are_preserved(
    initializer: Initializer,
    asset_directory: Path,
    destination: Path,
) -> None:
    initializer.initialize(destination=destination, project_root=destination)

    for name in ASSET_NAMES[1:]:
        assert (destination / name).read_bytes() == (asset_directory / name).read_bytes()


def test_generated_config_resolves_the_root_and_companion_files(
    initializer: Initializer,
    destination: Path,
    project_root: Path,
    tmp_path: Path,
) -> None:
    initializer.initialize(destination=destination, project_root=project_root)

    config = Config.from_path(
        config_file=destination / "checksmith.yaml",
        working_directory=tmp_path,
    )
    assert config.project_root == project_root
    referenced_files = {
        argument.config_path
        for check in config.checks
        for argument in check.args
        if isinstance(argument, ConfigPath)
    }
    assert referenced_files == {
        destination / name for name in ASSET_NAMES if name != "checksmith.yaml"
    }
    assert all(path.is_file() for path in referenced_files)
    assert not tuple(project_root.iterdir())


def test_relative_paths_resolve_against_the_destination(
    initializer: Initializer,
    tmp_path: Path,
    destination: Path,
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)

    output = initializer.initialize(
        destination=Path("configuration"),
        project_root=Path("../project/../project"),
    )

    assert output.project_root == project_root
    assert output.created_files == tuple(destination / name for name in ASSET_NAMES)
    assert yaml.safe_load((destination / "checksmith.yaml").read_bytes())[
        "project_root"
    ] == "../project"


def test_root_symlinks_keep_the_selected_lexical_path(
    initializer: Initializer,
    tmp_path: Path,
    destination: Path,
    project_root: Path,
) -> None:
    selected_root = tmp_path / "project alias"
    selected_root.symlink_to(project_root, target_is_directory=True)

    output = initializer.initialize(destination=destination, project_root=selected_root)

    assert output.project_root == selected_root
    assert yaml.safe_load((destination / "checksmith.yaml").read_bytes())[
        "project_root"
    ] == "../project alias"


def test_only_manifest_assets_are_copied_and_unrelated_files_remain(
    initializer: Initializer,
    asset_directory: Path,
    destination: Path,
) -> None:
    (asset_directory / "extra.txt").write_text("not a starter file", encoding="utf-8")
    cache = asset_directory / "__pycache__"
    cache.mkdir()
    (cache / "example.pyc").write_bytes(b"cache")
    unrelated = destination / "notes.txt"
    unrelated.write_bytes(b"keep these notes")

    initializer.initialize(destination=destination, project_root=destination)

    assert {path.name for path in destination.iterdir()} == {*ASSET_NAMES, "notes.txt"}
    assert unrelated.read_bytes() == b"keep these notes"


@pytest.mark.parametrize("name", ASSET_NAMES)
@pytest.mark.parametrize("collision_kind", ["file", "directory", "dangling symlink"])
def test_all_collisions_fail_before_creating_any_files(
    initializer: Initializer,
    destination: Path,
    name: str,
    collision_kind: str,
) -> None:
    collision = destination / name
    if collision_kind == "file":
        collision.write_bytes(b"existing content")
    elif collision_kind == "directory":
        collision.mkdir()
        (collision / "keep.txt").write_bytes(b"existing content")
    else:
        collision.symlink_to(destination / "missing-target")

    with pytest.raises(ChecksmithError) as raised:
        initializer.initialize(destination=destination, project_root=destination)

    assert str(collision) in str(raised.value)
    assert tuple(destination.iterdir()) == (collision,)
    if collision_kind == "file":
        assert collision.read_bytes() == b"existing content"
    elif collision_kind == "directory":
        assert (collision / "keep.txt").read_bytes() == b"existing content"
    else:
        assert collision.is_symlink()
        assert collision.readlink() == destination / "missing-target"


def test_a_file_created_after_preflight_is_never_overwritten(
    asset_directory: Path,
    destination: Path,
) -> None:
    raced = destination / "ruff.toml"
    initializer = Initializer(
        assets=PackagedAssetSource(directory=asset_directory),
        renderer=YamlConfigRenderer(),
        filesystem=CompetingFilesystem(
            filesystem=LocalInitializationFilesystem(), raced=raced
        ),
    )

    with pytest.raises(ChecksmithError) as raised:
        initializer.initialize(destination=destination, project_root=destination)

    assert str(raced) in str(raised.value)
    assert raced.read_bytes() == b"created by another process"
    assert {path.name for path in destination.iterdir()} == {
        "checksmith.yaml",
        "ruff.toml",
    }


def test_renderer_preserves_all_content_except_the_root_scalar(
    template: bytes,
    destination: Path,
) -> None:
    rendered = YamlConfigRenderer().render(
        content=template,
        config_file=destination / "checksmith.yaml",
        project_root=destination,
    )

    assert rendered == template.replace(b"project_root: ../../..", b'project_root: "."')


@pytest.mark.parametrize(
    "root_name",
    [
        "project space",
        "project: # comment",
        "quote'and\"double",
        "line\nbreak",
        "café",
        "project 🐍",
        "next\u0085line",
    ],
)
def test_renderer_safely_encodes_root_names_without_filesystem_access(
    template: bytes,
    destination: Path,
    root_name: str,
) -> None:
    project_root = destination / root_name
    config_file = destination / "checksmith.yaml"

    rendered = YamlConfigRenderer().render(
        content=template,
        config_file=config_file,
        project_root=project_root,
    )

    document: object = yaml.safe_load(rendered)
    assert isinstance(document, Mapping)
    assert document["project_root"] == root_name
    assert Config.from_mapping(
        document=document, config_file=config_file
    ).project_root == project_root
    assert not tuple(destination.iterdir())


@pytest.mark.parametrize(
    "content",
    [b"", b"[]\n", b"checks: [\n", b"\xff\xfe", b"---\n{}\n---\n{}\n"],
)
def test_renderer_rejects_unreadable_or_malformed_yaml(
    destination: Path,
    content: bytes,
) -> None:
    with pytest.raises(ChecksmithError):
        YamlConfigRenderer().render(
            content=content,
            config_file=destination / "checksmith.yaml",
            project_root=destination,
        )


@pytest.mark.parametrize(
    "replacement",
    [
        b"",
        b"project_root: .\nproject_root: ..\n",
        b"project_root: [..]\n",
        b"project_root: {path: ..}\n",
        b"project_root: null\n",
        b"project_root: 42\n",
    ],
)
def test_renderer_rejects_invalid_template_roots(
    template: bytes,
    destination: Path,
    replacement: bytes,
) -> None:
    content = template.replace(b"project_root: ../../..\n", replacement)

    with pytest.raises(ChecksmithError, match="project_root"):
        YamlConfigRenderer().render(
            content=content,
            config_file=destination / "checksmith.yaml",
            project_root=destination,
        )


def test_renderer_rejects_schema_errors(
    template: bytes,
    destination: Path,
) -> None:
    content = template.replace(b"schema_version: 1", b"schema_version: 999")

    with pytest.raises(ChecksmithError, match="schema_version"):
        YamlConfigRenderer().render(
            content=content,
            config_file=destination / "checksmith.yaml",
            project_root=destination,
        )


def test_renderer_rejects_a_root_alias_without_rewriting_the_anchored_value(
    template: bytes,
    destination: Path,
) -> None:
    content = template.replace(
        b"project_root: ../../..\n",
        b"another_path: &root ../../..\nproject_root: *root\n",
    )

    with pytest.raises(ChecksmithError, match="project_root"):
        YamlConfigRenderer().render(
            content=content,
            config_file=destination / "checksmith.yaml",
            project_root=destination,
        )


def test_asset_source_reads_the_injected_directory_exactly(tmp_path: Path) -> None:
    content = b"\x00\xffarbitrary asset bytes\r\n"
    (tmp_path / "fixture.bin").write_bytes(content)

    assert PackagedAssetSource(directory=tmp_path).read(name="fixture.bin") == content


def test_asset_source_reads_packaged_resources(template: bytes) -> None:
    source = PackagedAssetSource(directory=files("checksmith") / "assets" / "default")

    assert source.read(name="checksmith.yaml") == template


@pytest.mark.parametrize("asset_kind", ["missing", "directory"])
def test_asset_source_propagates_read_errors(
    tmp_path: Path, asset_kind: str
) -> None:
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


def test_prepared_files_are_immutable(tmp_path: Path) -> None:
    prepared = PreparedFile(path=tmp_path / "file", content=b"original")

    with pytest.raises(ValidationError, match="frozen"):
        prepared.content = b"changed"  # ty: ignore[invalid-assignment]
    assert prepared.content == b"original"
