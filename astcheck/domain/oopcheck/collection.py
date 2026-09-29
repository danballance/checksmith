import ast
from enum import Enum, auto

from pydantic import BaseModel, ConfigDict

from astcheck.domain.models import AnalysisModule, SourceLocation


class CallableCategory(Enum):
    STANDALONE = auto()
    METHOD = auto()


class LexicalOwner(Enum):
    MODULE = auto()
    CLASS = auto()
    FUNCTION = auto()


class StandaloneFunction(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    node: ast.FunctionDef | ast.AsyncFunctionDef
    location: SourceLocation
    statements: int


class ModuleMeasurements(BaseModel):
    model_config = ConfigDict(frozen=True)

    standalone_statements: int
    method_statements: int
    functions: tuple[StandaloneFunction, ...]


class ModuleCollector:
    def collect(self, *, module: AnalysisModule) -> ModuleMeasurements:
        return ModuleVisitor(path=module.path).collect(tree=module.tree)


class ModuleVisitor:
    def __init__(self, path: str) -> None:
        self._path = path
        self._standalone_statements = 0
        self._method_statements = 0
        self._functions: list[StandaloneFunction] = []

    def collect(self, *, tree: ast.Module) -> ModuleMeasurements:
        self._visit(node=tree, category=None, owner=LexicalOwner.MODULE)
        return ModuleMeasurements(
            standalone_statements=self._standalone_statements,
            method_statements=self._method_statements,
            functions=tuple(self._functions),
        )

    def _visit(
        self,
        *,
        node: ast.AST,
        category: CallableCategory | None,
        owner: LexicalOwner,
    ) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            self._visit_function(node=node, category=category, owner=owner)
            return
        if isinstance(node, ast.ClassDef):
            for statement in node.body:
                self._visit(node=statement, category=None, owner=LexicalOwner.CLASS)
            return
        if isinstance(node, ast.Pass):
            return
        if (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and (isinstance(node.value.value, str) or node.value.value is Ellipsis)
        ):
            return
        if isinstance(node, ast.stmt):
            if category is CallableCategory.STANDALONE:
                self._standalone_statements += 1
            elif category is CallableCategory.METHOD:
                self._method_statements += 1
        for child in ast.iter_child_nodes(node):
            self._visit(node=child, category=category, owner=owner)

    def _visit_function(
        self,
        *,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        category: CallableCategory | None,
        owner: LexicalOwner,
    ) -> None:
        if owner is LexicalOwner.MODULE:
            function_category = CallableCategory.STANDALONE
        elif owner is LexicalOwner.CLASS:
            function_category = CallableCategory.METHOD
        else:
            function_category = category
        before = self._standalone_statements
        for statement in node.body:
            self._visit(
                node=statement,
                category=function_category,
                owner=LexicalOwner.FUNCTION,
            )
        if owner is LexicalOwner.MODULE:
            self._functions.append(
                StandaloneFunction(
                    node=node,
                    location=SourceLocation(
                        path=self._path,
                        line=node.lineno,
                        column=node.col_offset + 1,
                    ),
                    statements=self._standalone_statements - before,
                )
            )
