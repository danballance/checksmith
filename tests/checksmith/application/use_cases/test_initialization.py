from collections.abc import Mapping
from pathlib import Path

import pytest
import yaml
from pydantic import BaseModel

from checksmith.adapters.driven.configuration.initialization import YamlConfigRenderer
from checksmith.adapters.driven.configuration.loading import (
    ConfigLoader,
    LocalYamlConfigSource,
)
from checksmith.adapters.driven.filesystem.initialization import (
    LocalInitializationFilesystem,
    PackagedAssetSource,
)
from checksmith.application.ports.initialization import (
    PreparedConfiguration,
    PreparedFile,
)
from checksmith.application.use_cases.initialization import Initializer
from checksmith.domain.config import ConfigPath
from checksmith.domain.errors import ChecksmithError
from checksmith.domain.models import CommandName, PackageType
from tests.checksmith.conftest import ConfigureCall, RecordingConfigurator

ASSET_NAMES = Initializer.ASSET_NAMES
FILE_NAMES = ASSET_NAMES + ("astcheck.yaml",)


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
    ) -> PreparedConfiguration:
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
        return PreparedConfiguration(
            content=self.rendered, astcheck_package="checksmith==1.2.3"
        )


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
    def __init__(self, filesystem: LocalInitializationFilesystem, raced: Path) -> None:
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
    configurator: RecordingConfigurator,
) -> Initializer:
    return Initializer(
        assets=asset_source,
        renderer=renderer,
        filesystem=filesystem,
        astcheck=configurator,
    )


def test_orchestration_prepares_every_asset_before_writing(
    orchestrator: Initializer,
    asset_source: RecordingAssetSource,
    renderer: RecordingConfigRenderer,
    filesystem: RecordingFilesystem,
    events: list[str],
    destination: Path,
    project_root: Path,
) -> None:
    output = orchestrator.initialize(
        astcheck_policy=None,
        destination=destination,
        project_root=project_root,
    )

    assert Initializer.ASSET_NAMES == ASSET_NAMES
    assert output.project_root == project_root
    assert output.created_files == tuple(destination / name for name in FILE_NAMES)
    assert renderer.calls == [
        RenderCall(
            content=asset_source.contents["checksmith.yaml"],
            config_file=destination / "checksmith.yaml",
            project_root=project_root,
        )
    ]
    assert filesystem.written[:-1] == [
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
    assert filesystem.written[-1].path.name == "astcheck.yaml"
    assert filesystem.written[-1].content == b"generated ASTcheck YAML\n"
    assert events == [
        f"directory:{destination}",
        f"directory:{project_root}",
        *(f"exists:{name}" for name in FILE_NAMES),
        "read:checksmith.yaml",
        "render:checksmith.yaml",
        *(f"read:{name}" for name in ASSET_NAMES[1:]),
        "configure:astcheck.yaml",
        *(f"write:{name}" for name in FILE_NAMES),
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
        orchestrator.initialize(
            astcheck_policy=None,
            destination=destination,
            project_root=project_root,
        )

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
        orchestrator.initialize(
            astcheck_policy=None,
            destination=destination,
            project_root=project_root,
        )

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
        orchestrator.initialize(
            astcheck_policy=None,
            destination=destination,
            project_root=project_root,
        )

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
        orchestrator.initialize(
            astcheck_policy=None,
            destination=destination,
            project_root=project_root,
        )

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
        orchestrator.initialize(
            astcheck_policy=None,
            destination=destination,
            project_root=project_root,
        )

    assert all(str(path) in str(raised.value) for path in filesystem.entries)
    assert events == [
        f"directory:{destination}",
        f"directory:{project_root}",
        *(f"exists:{name}" for name in FILE_NAMES),
    ]
    assert not filesystem.written


@pytest.mark.parametrize("name", FILE_NAMES)
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
        orchestrator.initialize(
            astcheck_policy=None,
            destination=destination,
            project_root=project_root,
        )

    assert str(filesystem.refused) in str(raised.value)
    assert "creation denied" in str(raised.value)
    assert isinstance(raised.value.__cause__, PermissionError)
    assert events[-1] == f"write:{name}"
    assert [written.path.name for written in filesystem.written] == list(
        FILE_NAMES[: FILE_NAMES.index(name)]
    )


def test_initialization_writes_exactly_the_five_starter_files(
    initializer: Initializer,
    destination: Path,
) -> None:
    output = initializer.initialize(
        astcheck_policy=None,
        destination=destination,
        project_root=destination,
    )

    expected_files = tuple(destination / name for name in FILE_NAMES)
    assert output.project_root == destination
    assert output.created_files == expected_files
    assert set(destination.iterdir()) == set(expected_files)
    assert (
        yaml.safe_load((destination / "checksmith.yaml").read_bytes())["project_root"]
        == "."
    )


def test_supporting_files_are_preserved(
    initializer: Initializer,
    asset_directory: Path,
    destination: Path,
) -> None:
    initializer.initialize(
        astcheck_policy=None,
        destination=destination,
        project_root=destination,
    )

    for name in ASSET_NAMES[1:]:
        assert (destination / name).read_bytes() == (
            asset_directory / name
        ).read_bytes()


def test_generated_config_resolves_the_root_and_companion_files(
    initializer: Initializer,
    destination: Path,
    project_root: Path,
    tmp_path: Path,
) -> None:
    initializer.initialize(
        astcheck_policy=None,
        destination=destination,
        project_root=project_root,
    )

    config = ConfigLoader(source=LocalYamlConfigSource()).load(
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
        destination / name for name in FILE_NAMES if name != "checksmith.yaml"
    }
    assert all(path.is_file() for path in referenced_files)
    assert not tuple(project_root.iterdir())


def test_generated_config_preserves_explicit_complexipy_defaults(
    initializer: Initializer,
    destination: Path,
    project_root: Path,
    tmp_path: Path,
) -> None:
    initializer.initialize(
        astcheck_policy=None,
        destination=destination,
        project_root=project_root,
    )
    config = ConfigLoader(source=LocalYamlConfigSource()).load(
        config_file=destination / "checksmith.yaml",
        working_directory=tmp_path,
    )
    complexity = next(
        check for check in config.checks if check.command is CommandName.COMPLEXIPY
    )

    assert complexity.id == "complexipy"
    assert complexity.package == "complexipy==8.0.1"
    assert complexity.arguments == (
        "--plain",
        "--failed",
        "--max-complexity-allowed",
        "10",
        "--exclude",
        "tests/**",
        "--color",
        "no",
        "--snapshot-ignore",
        "--snapshot-create=false",
        "--ignore-complexity=false",
        "--report-ignored=false",
        ".",
    )
    assert config.project_root == project_root
    assert {path.name for path in destination.iterdir()} == set(FILE_NAMES)
    assert not tuple(project_root.iterdir())


def test_generated_config_includes_module_and_package_architecture_checks(
    initializer: Initializer,
    destination: Path,
    project_root: Path,
) -> None:
    initializer.initialize(
        astcheck_policy=None,
        destination=destination,
        project_root=project_root,
    )
    config = ConfigLoader(source=LocalYamlConfigSource()).load(
        config_file=destination / "checksmith.yaml",
        working_directory=destination,
    )
    architecture = tuple(
        check for check in config.checks if check.command is CommandName.PYARCHGRAPH
    )

    assert tuple(check.id for check in architecture) == (
        "pyarchgraph-modules",
        "pyarchgraph-packages",
    )
    assert tuple(check.arguments for check in architecture) == (
        (".", "--gate", "structural"),
        (".", "--gate", "package-structural"),
    )
    assert all(check.package_type is PackageType.UVX for check in architecture)
    assert all(
        check.package
        == "pyarchgraph @ git+https://github.com/danballance/pyarchgraph@main"
        for check in architecture
    )


def test_relative_paths_resolve_against_the_destination(
    initializer: Initializer,
    tmp_path: Path,
    destination: Path,
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)

    output = initializer.initialize(
        astcheck_policy=None,
        destination=Path("configuration"),
        project_root=Path("../project/../project"),
    )

    assert output.project_root == project_root
    assert output.created_files == tuple(destination / name for name in FILE_NAMES)
    assert (
        yaml.safe_load((destination / "checksmith.yaml").read_bytes())["project_root"]
        == "../project"
    )


def test_root_symlinks_keep_the_selected_lexical_path(
    initializer: Initializer,
    tmp_path: Path,
    destination: Path,
    project_root: Path,
) -> None:
    selected_root = tmp_path / "project alias"
    selected_root.symlink_to(project_root, target_is_directory=True)

    output = initializer.initialize(
        astcheck_policy=None,
        destination=destination,
        project_root=selected_root,
    )

    assert output.project_root == selected_root
    assert (
        yaml.safe_load((destination / "checksmith.yaml").read_bytes())["project_root"]
        == "../project alias"
    )


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

    initializer.initialize(
        astcheck_policy=None,
        destination=destination,
        project_root=destination,
    )

    assert {path.name for path in destination.iterdir()} == {*FILE_NAMES, "notes.txt"}
    assert unrelated.read_bytes() == b"keep these notes"


@pytest.mark.parametrize("name", FILE_NAMES)
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
        initializer.initialize(
            astcheck_policy=None,
            destination=destination,
            project_root=destination,
        )

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
    configurator: RecordingConfigurator,
) -> None:
    raced = destination / "ruff.toml"
    initializer = Initializer(
        astcheck=configurator,
        assets=PackagedAssetSource(directory=asset_directory),
        renderer=YamlConfigRenderer(),
        filesystem=CompetingFilesystem(
            filesystem=LocalInitializationFilesystem(), raced=raced
        ),
    )

    with pytest.raises(ChecksmithError) as raised:
        initializer.initialize(
            astcheck_policy=None,
            destination=destination,
            project_root=destination,
        )

    assert str(raced) in str(raised.value)
    assert raced.read_bytes() == b"created by another process"
    assert {path.name for path in destination.iterdir()} == {
        "checksmith.yaml",
        "ruff.toml",
    }


def test_external_configuration_is_preserved_and_policy_path_is_absolute(
    initializer: Initializer,
    configurator: RecordingConfigurator,
    destination: Path,
    project_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    configurator.configuration = b"custom YAML response: preserved exactly\n"

    initializer.initialize(
        destination=destination,
        project_root=project_root,
        astcheck_policy=Path("policy.yaml"),
    )

    assert (destination / "astcheck.yaml").read_bytes() == configurator.configuration
    assert configurator.calls == [
        ConfigureCall(
            package="checksmith @ git+https://github.com/danballance/checksmith@main",
            project_root=project_root,
            config_file=destination / "astcheck.yaml",
            policy=tmp_path / "policy.yaml",
        )
    ]


def test_external_setup_failure_stops_before_any_writes(
    initializer: Initializer,
    configurator: RecordingConfigurator,
    destination: Path,
    project_root: Path,
) -> None:
    configurator.error = ChecksmithError("unsupported configure command")

    with pytest.raises(ChecksmithError, match="unsupported configure command"):
        initializer.initialize(
            destination=destination,
            project_root=project_root,
            astcheck_policy=None,
        )

    assert not tuple(destination.iterdir())
