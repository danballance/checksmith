import ast

from astcheck.domain.errors import AstcheckError
from astcheck.domain.models import AnalysisModule, SourceLocation


class PythonAstParser:
    def parse(self, *, path: str, source: str) -> AnalysisModule:
        try:
            tree = ast.parse(source, filename=path)
        except SyntaxError as error:
            raise AstcheckError(
                f"Could not parse {path}: {error.msg}",
                SourceLocation(
                    path=path,
                    line=error.lineno or 1,
                    column=max(error.offset or 1, 1),
                ),
            ) from error
        return AnalysisModule(path=path, source=source, tree=tree)
