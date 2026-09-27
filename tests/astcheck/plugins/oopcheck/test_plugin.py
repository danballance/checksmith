import ast
from pathlib import Path

import pytest
from pydantic import JsonValue, ValidationError

from astcheck.domain.errors import AstcheckError
from astcheck.domain.models import AnalysisModule, AnalysisProject, Finding
from astcheck.plugins.oopcheck.plugin import OopcheckPlugin


@pytest.fixture
def settings() -> dict[str, JsonValue]:
    return {
        "max_standalone_percent": 25,
        "min_callable_statements": 20,
        "min_shared_functions": 3,
        "collaborators": [{"name": "database", "annotations": ["Session"]}],
    }


def analyze(source: str, settings: dict[str, JsonValue]) -> tuple[Finding, ...]:
    project = AnalysisProject(
        root=Path("/project"),
        modules=(
            AnalysisModule(path="sample.py", source=source, tree=ast.parse(source)),
        ),
    )
    return OopcheckPlugin().create(settings=settings).analyze(project=project)


@pytest.mark.parametrize(
    ("standalone", "methods", "expected"),
    [(5, 15, False), (6, 14, True), (19, 0, False), (20, 0, True), (0, 20, False)],
)
def test_statement_share_thresholds_are_exact(
    settings: dict[str, JsonValue], standalone: int, methods: int, expected: bool
) -> None:
    source = "def helper():\n"
    source += "    consume()\n" * standalone if standalone else "    pass\n"
    source += "class Service:\n    def run(self):\n"
    source += "        consume()\n" * methods if methods else "        pass\n"
    findings = analyze(source, settings)
    assert bool(findings) is expected
    if expected:
        finding = findings[0]
        assert finding.rule_id == "standalone-statement-share"
        assert f"{standalone} of {standalone + methods}" in finding.message
        assert "Standalone functions (helper)" in finding.message
        assert "minimum of 20 callable statements" in finding.message
        assert finding.location.line == 1


def test_small_modules_still_fail_the_independent_collaborator_rule(
    settings: dict[str, JsonValue],
) -> None:
    findings = analyze(
        "def first(db: Session): return db\n"
        "def second(db: Session): return db\n"
        "async def third(db: 'Session'): return db\n",
        settings,
    )
    assert len(findings) == 1
    finding = findings[0]
    assert finding.rule_id == "repeated-collaborator"
    assert "3 standalone functions (first, second, third)" in finding.message
    assert finding.location.line == 1
    assert [location.line for location in finding.related_locations] == [2, 3]


def test_multiple_parameters_count_one_function_and_below_threshold_passes(
    settings: dict[str, JsonValue],
) -> None:
    source = (
        "def first(a: Session, b: Session): return a\n"
        "def second(a: Session): return a\n"
    )
    assert analyze(source, settings) == ()


def test_all_parameter_kinds_match_and_return_annotations_do_not(
    settings: dict[str, JsonValue],
) -> None:
    source = (
        "def first(a: Session, /): return a\n"
        "def second(*, a: Session): return a\n"
        "def third(*a: Session): return a\n"
        "def fourth(**a: Session): return a\n"
        "def fifth() -> Session: return None\n"
        "def sixth(a): return a\n"
    )
    findings = analyze(source, settings)
    assert len(findings) == 1
    assert "4 standalone functions (first, second, third, fourth)" in findings[0].message


def test_aliases_and_wrappers_only_match_explicitly_configured_spellings(
    settings: dict[str, JsonValue],
) -> None:
    source = (
        "from elsewhere import Session as Alias\n"
        "def first(a: Alias): return a\n"
        "def second(a: Optional[Session]): return a\n"
        "def third(a: Annotated[Session, 'metadata']): return a\n"
    )
    assert analyze(source, settings) == ()
    settings["collaborators"] = [
        {
            "name": "database",
            "annotations": ["Alias", "Optional[Session]", "Annotated[Session, 'metadata']"],
        }
    ]
    findings = analyze(source, settings)
    assert len(findings) == 1
    assert findings[0].rule_id == "repeated-collaborator"


def test_nested_helpers_methods_and_overload_stubs_do_not_count_as_functions(
    settings: dict[str, JsonValue],
) -> None:
    source = '''
@overload
def public(a: Session): ...
@overload
def public(a: Session): pass
def public(a: Session):
    def nested(a: Session):
        return a
    return nested(a)
class Service:
    def one(self, a: Session): return a
    def two(self, a: Session): return a
'''
    assert analyze(source, settings) == ()


def test_groups_match_independently_and_findings_keep_configuration_order(
    settings: dict[str, JsonValue],
) -> None:
    settings["collaborators"] = [
        {"name": "cache", "annotations": ["Cache"]},
        {"name": "database", "annotations": ["Session"]},
    ]
    source = (
        "def first(db: Session, cache: Cache): return db\n"
        "def second(db: Session, cache: Cache): return db\n"
        "def third(db: Session, cache: Cache): return db\n"
    )
    findings = analyze(source, settings)
    assert len(findings) == 2
    assert "'cache'" in findings[0].message
    assert "'database'" in findings[1].message


def test_share_and_collaborator_findings_are_both_reported(
    settings: dict[str, JsonValue],
) -> None:
    settings["min_callable_statements"] = 3
    source = (
        "def first(db: Session): return db\n"
        "def second(db: Session): return db\n"
        "def third(db: Session): return db\n"
    )
    findings = analyze(source, settings)
    assert [finding.rule_id for finding in findings] == [
        "standalone-statement-share",
        "repeated-collaborator",
    ]
    assert "Standalone functions (first, second, third)" in findings[0].message
    assert [location.line for location in findings[0].related_locations] == [2, 3]


def test_invalid_forward_annotation_is_an_analysis_error_with_location(
    settings: dict[str, JsonValue],
) -> None:
    with pytest.raises(AstcheckError, match="Invalid annotation") as raised:
        analyze("def first(db: 'Session['): return db\n", settings)
    assert raised.value.location is not None
    assert raised.value.location.path == "sample.py"
    assert raised.value.location.line == 1
    assert raised.value.location.column == 15


def test_no_collaborator_groups_skips_annotation_matching(
    settings: dict[str, JsonValue],
) -> None:
    settings["collaborators"] = []
    assert analyze("def first(db: 'Session['): return db\n", settings) == ()


def test_factory_validates_settings_before_analysis() -> None:
    with pytest.raises(ValidationError):
        OopcheckPlugin().create(settings={})


def test_project_modules_and_repeated_runs_do_not_share_counts(
    settings: dict[str, JsonValue],
) -> None:
    source = "def first(db: Session): return db\ndef second(db: Session): return db\n"
    project = AnalysisProject(
        root=Path("/project"),
        modules=tuple(
            AnalysisModule(path=path, source=source, tree=ast.parse(source))
            for path in ("first.py", "second.py")
        ),
    )
    analyzer = OopcheckPlugin().create(settings=settings)
    assert analyzer.analyze(project=project) == ()
    assert analyzer.analyze(project=project) == ()


def test_analysis_leaves_source_and_shared_ast_unchanged(
    settings: dict[str, JsonValue],
) -> None:
    source = "def first(db: 'Session'): return db\n"
    tree = ast.parse(source)
    before = ast.dump(tree, include_attributes=True)
    module = AnalysisModule(path="first.py", source=source, tree=tree)
    project = AnalysisProject(root=Path("/project"), modules=(module,))
    OopcheckPlugin().create(settings=settings).analyze(project=project)
    assert module.source == source
    assert ast.dump(module.tree, include_attributes=True) == before
