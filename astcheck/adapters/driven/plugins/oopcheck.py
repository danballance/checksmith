from collections.abc import Mapping

from pydantic import JsonValue

from astcheck.application.ports.analysis import ProjectAnalyzer
from astcheck.domain.oopcheck.analyzers import (
    OopcheckAnalyzer,
    RepeatedCollaboratorAnalyzer,
    StandaloneStatementAnalyzer,
)
from astcheck.domain.oopcheck.annotations import AnnotationNormalizer
from astcheck.domain.oopcheck.collection import ModuleCollector
from astcheck.domain.oopcheck.settings import OopcheckSettings


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
