import ast
from pathlib import Path

import pytest

import astcheck


@pytest.mark.parametrize(
    ("subdirectory", "forbidden"),
    [
        ("domain", ("astcheck.adapters", "astcheck.application", "astcheck.plugins")),
        ("application", ("astcheck.adapters", "astcheck.plugins")),
        ("plugins", ("astcheck.adapters", "astcheck.application")),
        ("", ("checksmith",)),
    ],
)
def test_dependencies_point_toward_core(
    subdirectory: str, forbidden: tuple[str, ...]
) -> None:
    package = Path(astcheck.__file__).parent
    for path in (package / subdirectory).rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                names = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = (node.module,)
            else:
                continue
            for name in names:
                assert not any(
                    name == prefix or name.startswith(f"{prefix}.")
                    for prefix in forbidden
                ), f"{path.relative_to(package)} imports {name}"
