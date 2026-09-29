from typing import Protocol

from astcheck.domain.errors import AstcheckError
from astcheck.domain.models import AnalysisProject, Finding, SourceLocation
from astcheck.domain.oopcheck.annotations import AnnotationNormalizer
from astcheck.domain.oopcheck.collection import (
    ModuleCollector,
    ModuleMeasurements,
    StandaloneFunction,
)
from astcheck.domain.oopcheck.settings import CollaboratorGroup


class ModuleAnalyzer(Protocol):
    def analyze(self, *, measurements: ModuleMeasurements) -> tuple[Finding, ...]: ...


class StandaloneStatementAnalyzer:
    def __init__(self, maximum_percent: int, minimum_statements: int) -> None:
        self._maximum_percent = maximum_percent
        self._minimum_statements = minimum_statements

    def analyze(self, *, measurements: ModuleMeasurements) -> tuple[Finding, ...]:
        standalone = measurements.standalone_statements
        total = standalone + measurements.method_statements
        if total < self._minimum_statements:
            return ()
        if standalone * 100 <= self._maximum_percent * total:
            return ()
        functions = tuple(
            function for function in measurements.functions if function.statements > 0
        )
        names = ", ".join(function.node.name for function in functions)
        return (
            Finding(
                rule_id="standalone-statement-share",
                message=(
                    f"Standalone functions ({names}) contain {standalone} of {total} "
                    "callable statements, exceeding the configured maximum share of "
                    f"{self._maximum_percent}% with a minimum of "
                    f"{self._minimum_statements} callable statements. "
                    "Review related behavior for cohesive "
                    "classes and constructor-injected collaborators."
                ),
                location=functions[0].location,
                related_locations=tuple(
                    function.location for function in functions[1:]
                ),
            ),
        )


class RepeatedCollaboratorAnalyzer:
    def __init__(
        self,
        groups: tuple[CollaboratorGroup, ...],
        minimum_functions: int,
        normalizer: AnnotationNormalizer,
    ) -> None:
        self._groups = groups
        self._minimum_functions = minimum_functions
        self._normalizer = normalizer
        self._keys = {
            group.name: frozenset(
                normalizer.spelling_key(spelling) for spelling in group.annotations
            )
            for group in groups
        }

    def analyze(self, *, measurements: ModuleMeasurements) -> tuple[Finding, ...]:
        if not self._groups:
            return ()
        annotated = tuple(
            (function, self._annotation_keys(function=function))
            for function in measurements.functions
            if function.statements > 0
        )
        findings: list[Finding] = []
        for group in self._groups:
            matching = tuple(
                function
                for function, keys in annotated
                if self._keys[group.name].intersection(keys)
            )
            if len(matching) < self._minimum_functions:
                continue
            names = ", ".join(function.node.name for function in matching)
            findings.append(
                Finding(
                    rule_id="repeated-collaborator",
                    message=(
                        f"Collaborator {group.name!r} appears in {len(matching)} "
                        f"standalone functions ({names}), reaching the configured "
                        f"minimum of {self._minimum_functions}. Review whether this "
                        "shared collaborator belongs in a cohesive class constructor."
                    ),
                    location=matching[0].location,
                    related_locations=tuple(
                        function.location for function in matching[1:]
                    ),
                )
            )
        return tuple(findings)

    def _annotation_keys(self, *, function: StandaloneFunction) -> frozenset[str]:
        arguments = function.node.args
        parameters = [*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs]
        if arguments.vararg is not None:
            parameters.append(arguments.vararg)
        if arguments.kwarg is not None:
            parameters.append(arguments.kwarg)
        keys: set[str] = set()
        for parameter in parameters:
            annotation = parameter.annotation
            if annotation is None:
                continue
            try:
                keys.add(self._normalizer.key(annotation))
            except ValueError as error:
                raise AstcheckError(
                    message=(
                        f"Invalid annotation for parameter {parameter.arg!r} in "
                        f"function {function.node.name!r}: {error}"
                    ),
                    location=SourceLocation(
                        path=function.location.path,
                        line=annotation.lineno,
                        column=annotation.col_offset + 1,
                    ),
                ) from error
        return frozenset(keys)


class OopcheckAnalyzer:
    def __init__(
        self,
        collector: ModuleCollector,
        analyzers: tuple[ModuleAnalyzer, ...],
    ) -> None:
        self._collector = collector
        self._analyzers = analyzers

    def analyze(self, *, project: AnalysisProject) -> tuple[Finding, ...]:
        findings: list[Finding] = []
        for module in project.modules:
            measurements = self._collector.collect(module=module)
            for analyzer in self._analyzers:
                findings.extend(analyzer.analyze(measurements=measurements))
        return tuple(findings)
