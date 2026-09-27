from collections.abc import Mapping
from importlib import metadata

import pytest
from pydantic import JsonValue

from astcheck.adapters.plugins import (
    EntryPointPluginRegistry,
    InstalledPluginCatalog,
    PluginEntryPoint,
)
from astcheck.domain.errors import AstcheckError
from astcheck.domain.models import AnalysisProject, Finding
from astcheck.ports import ProjectAnalyzer


class SampleAnalyzer:
    def analyze(self, *, project: AnalysisProject) -> tuple[Finding, ...]:
        return ()


class SampleFactory:
    def create(self, *, settings: Mapping[str, JsonValue]) -> ProjectAnalyzer:
        return SampleAnalyzer()


class MissingCreateFactory:
    pass


class NonCallableFactory:
    create = 1


class BrokenFactory:
    def __init__(self) -> None:
        raise RuntimeError("factory construction failed")


class FakeEntryPoint:
    def __init__(self, *, name: str, loaded: object, error: Exception | None) -> None:
        self._name = name
        self.loaded = loaded
        self.error = error
        self.loads = 0

    @property
    def name(self) -> str:
        return self._name

    def load(self) -> object:
        self.loads += 1
        if self.error is not None:
            raise self.error
        return self.loaded


class FakeCatalog:
    def __init__(self, entries: tuple[PluginEntryPoint, ...]) -> None:
        self._entries = entries

    def entries(self) -> tuple[PluginEntryPoint, ...]:
        return self._entries


def test_registry_loads_only_the_configured_plugin() -> None:
    selected = FakeEntryPoint(name="custom", loaded=SampleFactory, error=None)
    unused = FakeEntryPoint(
        name="unused", loaded=None, error=RuntimeError("unused plugin must not load")
    )
    registry = EntryPointPluginRegistry(catalog=FakeCatalog((unused, selected)))

    factory = registry.load(plugin_id="custom")

    assert isinstance(factory, SampleFactory)
    assert isinstance(factory.create(settings={"threshold": 2}), SampleAnalyzer)
    assert selected.loads == 1
    assert unused.loads == 0


def test_missing_plugin_fails_with_its_id() -> None:
    registry = EntryPointPluginRegistry(catalog=FakeCatalog(()))

    with pytest.raises(AstcheckError, match="plugin 'missing' is not installed"):
        registry.load(plugin_id="missing")


def test_duplicate_plugins_fail_before_importing_either() -> None:
    first = FakeEntryPoint(name="custom", loaded=SampleFactory, error=None)
    second = FakeEntryPoint(name="custom", loaded=SampleFactory, error=None)
    registry = EntryPointPluginRegistry(catalog=FakeCatalog((first, second)))

    with pytest.raises(AstcheckError, match="duplicate entry points"):
        registry.load(plugin_id="custom")

    assert first.loads == 0
    assert second.loads == 0


@pytest.mark.parametrize(
    ("loaded", "message"),
    [
        (SampleFactory(), "factory class"),
        (None, "factory class"),
        (MissingCreateFactory, "implement PluginFactory.create"),
        (NonCallableFactory, "implement PluginFactory.create"),
        (BrokenFactory, "factory construction failed"),
    ],
)
def test_invalid_factories_are_reported_with_plugin_identity(
    loaded: object, message: str
) -> None:
    entry = FakeEntryPoint(name="broken", loaded=loaded, error=None)
    registry = EntryPointPluginRegistry(catalog=FakeCatalog((entry,)))

    with pytest.raises(AstcheckError, match=message) as raised:
        registry.load(plugin_id="broken")

    assert "Could not load plugin 'broken'" in str(raised.value)
    assert raised.value.__cause__ is not None


def test_plugin_import_errors_are_reported() -> None:
    entry = FakeEntryPoint(
        name="broken", loaded=None, error=ImportError("missing dependency")
    )
    registry = EntryPointPluginRegistry(catalog=FakeCatalog((entry,)))

    with pytest.raises(AstcheckError, match="missing dependency"):
        registry.load(plugin_id="broken")


def test_installed_catalog_requests_only_astcheck_entry_points(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry = metadata.EntryPoint(
        name="sample", value="sample.plugin:Factory", group="astcheck.plugins"
    )
    requested: list[str] = []

    def installed_entries(*, group: str) -> metadata.EntryPoints:
        requested.append(group)
        return metadata.EntryPoints((entry,))

    monkeypatch.setattr(metadata, "entry_points", installed_entries)

    assert InstalledPluginCatalog().entries() == (entry,)
    assert requested == ["astcheck.plugins"]
