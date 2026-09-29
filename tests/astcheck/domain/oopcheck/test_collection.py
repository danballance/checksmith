import ast

from astcheck.domain.models import AnalysisModule
from astcheck.domain.oopcheck.collection import ModuleCollector, ModuleMeasurements


def measure(source: str) -> ModuleMeasurements:
    return ModuleCollector().collect(
        module=AnalysisModule(
            path="pkg/sample.py", source=source, tree=ast.parse(source)
        )
    )


def test_statement_counts_exclude_wrappers_and_noncallable_code() -> None:
    measured = measure("""
"module documentation"
module_value = 1
class Model:
    "class documentation"
    attribute: int = 3
    def run(self):
        "method documentation"
        if self.ready:
            return 1
        return 0
def helper():
    "function documentation"
    pass
    ...
    value = 1
    return value
""")
    assert measured.standalone_statements == 2
    assert measured.method_statements == 3
    assert [(f.node.name, f.statements) for f in measured.functions] == [("helper", 2)]


def test_nested_helpers_follow_owner_and_nested_class_methods_are_separate() -> None:
    measured = measure("""
def outer():
    def helper():
        return 1
    class Nested:
        setting = 0
        def method(self):
            def nested_helper():
                return 2
            return nested_helper()
    return helper()
class Service:
    def method(self):
        def helper():
            return 3
        return helper()
""")
    assert measured.standalone_statements == 2
    assert measured.method_statements == 4
    assert [(f.node.name, f.statements) for f in measured.functions] == [("outer", 2)]


def test_conditional_async_definitions_preserve_lexical_ownership() -> None:
    measured = measure("""
if enabled:
    async def fetch():
        await read()
        return 1
try:
    def load():
        return 2
except Exception:
    def recover():
        return 3
class Service:
    if enabled:
        async def run(self):
            await fetch()
""")
    assert measured.standalone_statements == 4
    assert measured.method_statements == 1
    assert [f.node.name for f in measured.functions] == ["fetch", "load", "recover"]
    assert measured.functions[0].location.path == "pkg/sample.py"
    assert measured.functions[0].location.line == 3
    assert measured.functions[0].location.column == 5


def test_protocol_overload_and_model_declarations_cannot_dilute_the_share() -> None:
    measured = measure("""
class Shape(Protocol):
    def draw(self): ...
    def save(self):
        "docstring only"
class Data(BaseModel):
    count: int
@overload
def convert(value: int): ...
@overload
def convert(value: str): pass
def convert(value):
    return value
""")
    assert measured.standalone_statements == 1
    assert measured.method_statements == 0
    assert [f.statements for f in measured.functions] == [0, 0, 1]


def test_all_concrete_method_kinds_count_including_constructors() -> None:
    measured = measure("""
class Service:
    def __init__(self, dependency):
        self.dependency = dependency
    @property
    def name(self):
        return "service"
    @staticmethod
    def parse(value):
        return value
    @classmethod
    def create(cls):
        return cls(None)
""")
    assert measured.standalone_statements == 0
    assert measured.method_statements == 4


def test_reusing_collector_does_not_retain_previous_module_counts() -> None:
    collector = ModuleCollector()
    source = "def first():\n    return 1\n"
    first = collector.collect(
        module=AnalysisModule(path="first.py", source=source, tree=ast.parse(source))
    )
    second = collector.collect(
        module=AnalysisModule(path="empty.py", source="", tree=ast.parse(""))
    )
    assert first.standalone_statements == 1
    assert second.standalone_statements == 0
    assert second.functions == ()
