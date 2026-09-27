from importlib import metadata
from typing import Protocol

from astcheck.domain.errors import AstcheckError
from astcheck.ports import PluginFactory


class PluginEntryPoint(Protocol):
    @property
    def name(self) -> str: ...

    def load(self) -> object: ...


class PluginCatalog(Protocol):
    def entries(self) -> tuple[PluginEntryPoint, ...]: ...


class InstalledPluginCatalog:
    def entries(self) -> tuple[PluginEntryPoint, ...]:
        return tuple(metadata.entry_points(group="astcheck.plugins"))


class EntryPointPluginRegistry:
    def __init__(self, *, catalog: PluginCatalog) -> None:
        self._catalog = catalog

    def load(self, *, plugin_id: str) -> PluginFactory:
        try:
            entries = tuple(
                entry for entry in self._catalog.entries() if entry.name == plugin_id
            )
            if not entries:
                raise ValueError(f"plugin {plugin_id!r} is not installed")
            if len(entries) != 1:
                raise ValueError(f"plugin {plugin_id!r} has duplicate entry points")
            candidate = entries[0].load()
            if not isinstance(candidate, type):
                raise TypeError("plugin entry point must expose a factory class")
            factory = candidate()
            if not isinstance(factory, PluginFactory) or not callable(factory.create):
                raise TypeError("plugin class must implement PluginFactory.create")
        except Exception as error:
            raise AstcheckError(f"Could not load plugin {plugin_id!r}: {error}", None) from error
        return factory
