import ast
from collections.abc import Mapping
from pathlib import Path
from typing import cast

import pytest
import yaml
from pydantic import BaseModel, ConfigDict, JsonValue, StrictStr

from astcheck.adapters.configuration import YamlConfigurationLoader
from astcheck.adapters.parser import PythonAstParser
from astcheck.adapters.sources import LocalSourceRepository
from astcheck.application.service import AnalysisService
from astcheck.domain.configuration import AnalysisPolicy
from astcheck.domain.errors import AstcheckError
from astcheck.domain.models import (
    AnalysisModule,
    AnalysisProject,
    Finding,
    SourceLocation,
)
from astcheck.ports import PluginFactory, ProjectAnalyzer


class NameSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: StrictStr


class NameAnalyzer:
    def __init__(self, *, settings: NameSettings) -> None:
        self._settings = settings

    def analyze(self, *, project: AnalysisProject) -> tuple[Finding, ...]:
        findings: list[Finding] = []
        for module in reversed(project.modules):
            for node in ast.walk(module.tree):
                if isinstance(node, ast.Name) and node.id == self._settings.name:
                    findings.append(
                        Finding(
                            rule_id="forbidden-name",
                            message=f"Avoid {self._settings.name}",
                            location=SourceLocation(
                                path=module.path,
                                line=node.lineno,
                                column=node.col_offset + 1,
                            ),
                            related_locations=(),
                        )
                    )
        return tuple(findings)


class NameFactory:
    def create(self, *, settings: Mapping[str, JsonValue]) -> ProjectAnalyzer:
        return NameAnalyzer(settings=NameSettings.model_validate(settings))


class FixedRegistry:
    def __init__(self, *, factories: Mapping[str, PluginFactory]) -> None:
        self.factories = factories
        self.loaded: list[str] = []

    def load(self, *, plugin_id: str) -> PluginFactory:
        self.loaded.append(plugin_id)
        return self.factories[plugin_id]


class RecordingParser(PythonAstParser):
    def __init__(self) -> None:
        self.parsed: list[str] = []

    def parse(self, *, path: str, source: str) -> AnalysisModule:
        self.parsed.append(path)
        return super().parse(path=path, source=source)


class FailingAnalyzer:
    def __init__(self, *, error: Exception) -> None:
        self._error = error

    def analyze(self, *, project: AnalysisProject) -> tuple[Finding, ...]:
        raise self._error


class FixedFactory:
    def __init__(self, *, analyzer: ProjectAnalyzer) -> None:
        self._analyzer = analyzer

    def create(self, *, settings: Mapping[str, JsonValue]) -> ProjectAnalyzer:
        return self._analyzer


class InvalidAnalyzer:
    def analyze(self, *, project: AnalysisProject) -> tuple[Finding, ...]:
        return cast(tuple[Finding, ...], object())


@pytest.fixture
def configuration(tmp_path: Path) -> Path:
    (tmp_path / "a.py").write_text("banned = 1\n", encoding="utf-8")
    (tmp_path / "z.py").write_text("banned = 2\n", encoding="utf-8")
    path = tmp_path / "astcheck.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "project_root": ".",
                "sources": ["."],
                "exclusions": [],
                "plugins": [
                    {"id": "z-plugin", "settings": {"name": "absent"}},
                    {"id": "a-plugin", "settings": {"name": "banned"}},
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def test_new_plugin_settings_and_rules_need_no_engine_changes(
    configuration: Path,
) -> None:
    registry = FixedRegistry(
        factories={"a-plugin": NameFactory(), "z-plugin": NameFactory()}
    )
    parser = RecordingParser()
    service = AnalysisService(
        configurations=YamlConfigurationLoader(),
        sources=LocalSourceRepository(),
        parser=parser,
        plugins=registry,
    )

    report = service.run(config_file=configuration)

    assert report.exit_code == 1
    assert report.modules == ("a.py", "z.py")
    assert registry.loaded == ["a-plugin", "z-plugin"]
    assert parser.parsed == ["a.py", "z.py"]
    assert [plugin.plugin_id for plugin in report.plugins] == ["a-plugin", "z-plugin"]
    assert [finding.location.path for finding in report.plugins[0].findings] == [
        "a.py", "z.py"
    ]
    assert report.plugins[1].findings == ()
    assert report.model_dump_json() == service.run(
        config_file=configuration
    ).model_dump_json()


def test_missing_plugin_fails_before_sources_are_read(configuration: Path) -> None:
    parser = RecordingParser()
    service = AnalysisService(
        configurations=YamlConfigurationLoader(),
        sources=LocalSourceRepository(),
        parser=parser,
        plugins=FixedRegistry(factories={}),
    )
    report = service.run(config_file=configuration)
    assert report.exit_code == 2
    assert "Could not configure plugin 'a-plugin'" in report.errors[0].message
    assert parser.parsed == []


def test_plugin_settings_validation_fails_before_scanning(configuration: Path) -> None:
    configuration.write_text(
        configuration.read_text().replace("name: banned", "unknown: banned"),
        encoding="utf-8",
    )
    parser = RecordingParser()
    service = AnalysisService(
        configurations=YamlConfigurationLoader(),
        sources=LocalSourceRepository(),
        parser=parser,
        plugins=FixedRegistry(factories={"a-plugin": NameFactory()}),
    )
    report = service.run(config_file=configuration)
    assert report.exit_code == 2
    assert "validation error" in report.errors[0].message
    assert parser.parsed == []


@pytest.mark.parametrize(
    "error",
    [
        RuntimeError("broken analysis"),
        AstcheckError(
            "bad annotation", SourceLocation(path="a.py", line=1, column=1)
        ),
    ],
)
def test_plugin_failures_preserve_plugin_and_location(
    configuration: Path, error: Exception
) -> None:
    factory = FixedFactory(analyzer=FailingAnalyzer(error=error))
    service = AnalysisService(
        configurations=YamlConfigurationLoader(),
        sources=LocalSourceRepository(),
        parser=PythonAstParser(),
        plugins=FixedRegistry(factories={"a-plugin": factory, "z-plugin": factory}),
    )
    report = service.run(config_file=configuration)
    assert report.exit_code == 2
    assert "Plugin 'a-plugin' failed" in report.errors[0].message
    assert str(error) in report.errors[0].message
    assert report.errors[0].path == (
        "a.py" if isinstance(error, AstcheckError) else None
    )
    assert report.plugins == ()


@pytest.mark.parametrize(
    ("analyzer", "message"),
    [
        (cast(ProjectAnalyzer, object()), "must return a ProjectAnalyzer"),
        (InvalidAnalyzer(), "must return a tuple of Finding"),
    ],
)
def test_invalid_plugin_contracts_are_errors(
    configuration: Path, analyzer: ProjectAnalyzer, message: str
) -> None:
    factory = FixedFactory(analyzer=analyzer)
    service = AnalysisService(
        configurations=YamlConfigurationLoader(),
        sources=LocalSourceRepository(),
        parser=PythonAstParser(),
        plugins=FixedRegistry(factories={"a-plugin": factory, "z-plugin": factory}),
    )
    report = service.run(config_file=configuration)
    assert report.exit_code == 2
    assert message in report.errors[0].message


def test_policy_validation_does_not_require_sources(configuration: Path) -> None:
    service = AnalysisService(
        configurations=YamlConfigurationLoader(),
        sources=LocalSourceRepository(),
        parser=PythonAstParser(),
        plugins=FixedRegistry(factories={"names": NameFactory()}),
    )
    service.validate_policy(
        policy=AnalysisPolicy.model_validate(
            {
                "sources": ["not-created-yet"],
                "exclusions": [],
                "plugins": [{"id": "names", "settings": {"name": "banned"}}],
            }
        )
    )


def test_source_syntax_error_keeps_location(configuration: Path) -> None:
    (configuration.parent / "a.py").write_text("def invalid(\n", encoding="utf-8")
    service = AnalysisService(
        configurations=YamlConfigurationLoader(),
        sources=LocalSourceRepository(),
        parser=PythonAstParser(),
        plugins=FixedRegistry(
            factories={"a-plugin": NameFactory(), "z-plugin": NameFactory()}
        ),
    )
    report = service.run(config_file=configuration)
    assert report.exit_code == 2
    assert report.errors[0].path == "a.py"
    assert report.errors[0].line == 1
