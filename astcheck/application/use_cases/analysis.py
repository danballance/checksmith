import logging
from pathlib import Path

from pydantic import ConfigDict

from astcheck.application.ports.analysis import (
    ConfigurationLoader,
    PluginRegistry,
    ProjectAnalyzer,
    SourceParser,
    SourceRepository,
)
from astcheck.domain.configuration import AnalysisPolicy
from astcheck.domain.errors import AstcheckError
from astcheck.domain.models import (
    AnalysisError,
    AnalysisProject,
    AnalysisReport,
    Finding,
    PluginReport,
    ValueModel,
)

logger = logging.getLogger(__name__)


class ConfiguredAnalyzer(ValueModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    plugin_id: str
    analyzer: ProjectAnalyzer


class AnalysisService:
    def __init__(
        self,
        *,
        configurations: ConfigurationLoader,
        sources: SourceRepository,
        parser: SourceParser,
        plugins: PluginRegistry,
    ) -> None:
        self._configurations = configurations
        self._sources = sources
        self._parser = parser
        self._plugins = plugins

    def validate_policy(self, *, policy: AnalysisPolicy) -> None:
        self._configure(policy=policy)

    def _configure(self, *, policy: AnalysisPolicy) -> tuple[ConfiguredAnalyzer, ...]:
        analyzers: list[ConfiguredAnalyzer] = []
        for configuration in sorted(policy.plugins, key=lambda item: item.id):
            try:
                factory = self._plugins.load(plugin_id=configuration.id)
                analyzer = factory.create(settings=configuration.settings)
                if not isinstance(analyzer, ProjectAnalyzer) or not callable(
                    analyzer.analyze
                ):
                    raise TypeError("plugin factory must return a ProjectAnalyzer")
            except Exception as error:
                raise AstcheckError(
                    f"Could not configure plugin {configuration.id!r}: {error}", None
                ) from error
            analyzers.append(
                ConfiguredAnalyzer(plugin_id=configuration.id, analyzer=analyzer)
            )
        return tuple(analyzers)

    def run(self, *, config_file: Path) -> AnalysisReport:
        try:
            configuration = self._configurations.load(path=config_file)
            analyzers = self._configure(policy=configuration)
            paths = self._sources.discover(
                root=configuration.project_root,
                sources=configuration.sources,
                exclusions=configuration.exclusions,
            )
            project = AnalysisProject(
                root=configuration.project_root,
                modules=tuple(
                    self._parser.parse(
                        path=path.relative_to(configuration.project_root).as_posix(),
                        source=self._sources.read(path=path),
                    )
                    for path in paths
                ),
            )
            reports = tuple(
                self._analyze(configured=configured, project=project)
                for configured in analyzers
            )
            return AnalysisReport(
                schema_version=1,
                status="complete",
                modules=tuple(module.path for module in project.modules),
                plugins=reports,
                errors=(),
            )
        except Exception as error:
            logger.debug("Analysis failed", exc_info=True)
            location = error.location if isinstance(error, AstcheckError) else None
            return AnalysisReport(
                schema_version=1,
                status="error",
                modules=(),
                plugins=(),
                errors=(
                    AnalysisError(
                        message=str(error) or type(error).__name__,
                        path=location.path if location else None,
                        line=location.line if location else None,
                        column=location.column if location else None,
                    ),
                ),
            )

    def _analyze(
        self, *, configured: ConfiguredAnalyzer, project: AnalysisProject
    ) -> PluginReport:
        try:
            findings = configured.analyzer.analyze(project=project)
            if not isinstance(findings, tuple) or any(
                not isinstance(finding, Finding) for finding in findings
            ):
                raise TypeError("analyze must return a tuple of Finding models")
            return PluginReport(
                plugin_id=configured.plugin_id,
                findings=tuple(sorted(findings, key=self._finding_key)),
            )
        except Exception as error:
            location = error.location if isinstance(error, AstcheckError) else None
            raise AstcheckError(
                f"Plugin {configured.plugin_id!r} failed: {error}", location
            ) from error

    @staticmethod
    def _finding_key(finding: Finding) -> tuple[str, int, int, str, str]:
        return (
            finding.location.path,
            finding.location.line,
            finding.location.column,
            finding.rule_id,
            finding.message,
        )
