import ast

import pytest

from astcheck.adapters.parser import PythonAstParser
from astcheck.domain.errors import AstcheckError


def test_source_is_parsed_without_executing_it() -> None:
    source = "raise RuntimeError('must not execute')\n"

    module = PythonAstParser().parse(path="src/app.py", source=source)

    assert module.path == "src/app.py"
    assert module.source == source
    assert isinstance(module.tree, ast.Module)
    assert isinstance(module.tree.body[0], ast.Raise)


@pytest.mark.parametrize("source", ["def broken(:\n", "\x00", "if True:\npass\n"])
def test_parse_errors_include_one_based_source_locations(source: str) -> None:
    with pytest.raises(AstcheckError, match="Could not parse src/broken.py") as raised:
        PythonAstParser().parse(path="src/broken.py", source=source)

    location = raised.value.location
    assert location is not None
    assert location.path == "src/broken.py"
    assert location.line >= 1
    assert location.column >= 1
    assert isinstance(raised.value.__cause__, SyntaxError)
