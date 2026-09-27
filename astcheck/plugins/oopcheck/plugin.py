from collections.abc import Mapping

from pydantic import JsonValue

from astcheck.plugins.oopcheck.analyzers import (
    OopcheckAnalyzer,
    RepeatedCollaboratorAnalyzer,
    StandaloneStatementAnalyzer,
)
from astcheck.plugins.oopcheck.annotations import AnnotationNormalizer
from astcheck.plugins.oopcheck.collection import ModuleCollector
from astcheck.plugins.oopcheck.settings import OopcheckSettings
from astcheck.ports import ProjectAnalyzer


class OopcheckPlugin:
    def create(self, *, settings: Mapping[str, JsonValue]) -> ProjectAnalyzer:
        configuration = OopcheckSettings.model_validate(dict(settings))
        return OopcheckAnalyzer(
            collector=ModuleCollector(),
            analyzers=(
                StandaloneStatementAnalyzer(
                    maximum_percent=configuration.max_standalone_percent,
                    minimum_statements=configuration.min_callable_statements,
                ),
                RepeatedCollaboratorAnalyzer(
                    groups=configuration.collaborators,
                    minimum_functions=configuration.min_shared_functions,
                    normalizer=AnnotationNormalizer(),
                ),
            ),
        )
