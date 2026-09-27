from collections.abc import Mapping
from pathlib import Path
from typing import Protocol, runtime_checkable

from pydantic import JsonValue

from astcheck.domain.configuration import AnalysisConfiguration
from astcheck.domain.models import AnalysisModule, AnalysisProject, Finding


@runtime_checkable
class ProjectAnalyzer(Protocol):
    def analyze(self, *, project: AnalysisProject) -> tuple[Finding, ...]: ...


@runtime_checkable
class PluginFactory(Protocol):
    def create(self, *, settings: Mapping[str, JsonValue]) -> ProjectAnalyzer: ...


class PluginRegistry(Protocol):
    def load(self, *, plugin_id: str) -> PluginFactory: ...


class ConfigurationLoader(Protocol):
    def load(self, *, path: Path) -> AnalysisConfiguration: ...


class SourceRepository(Protocol):
    def discover(
        self, *, root: Path, sources: tuple[str, ...], exclusions: tuple[str, ...]
    ) -> tuple[Path, ...]: ...

    def read(self, *, path: Path) -> str: ...


class SourceParser(Protocol):
    def parse(self, *, path: str, source: str) -> AnalysisModule: ...
