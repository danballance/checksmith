import ast

import pytest

from astcheck.plugins.oopcheck.annotations import AnnotationNormalizer


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("Client", " ( Client ) "),
        ("Client", "'Client'"),
        ("Client", "\"'Client'\""),
        ("list[Client]", "list [ Client ]"),
        ("Client | None", "'Client | None'"),
    ],
)
def test_normalization_ignores_formatting_and_outer_forward_strings(
    left: str, right: str
) -> None:
    normalizer = AnnotationNormalizer()
    assert normalizer.spelling_key(left) == normalizer.spelling_key(right)


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("Client", "Alias"),
        ("Client", "module.Client"),
        ("Client", "Optional[Client]"),
        ("Client", "Annotated[Client, 'metadata']"),
        ("Client | None", "None | Client"),
        ("list[Client]", "list['Client']"),
        ("Literal['Client']", "Literal[Client]"),
    ],
)
def test_aliases_wrappers_and_nested_strings_remain_explicit(
    left: str, right: str
) -> None:
    normalizer = AnnotationNormalizer()
    assert normalizer.spelling_key(left) != normalizer.spelling_key(right)


@pytest.mark.parametrize("spelling", ["", " ", "Client[", "'Client['", "\x00"])
def test_invalid_spelling_is_rejected(spelling: str) -> None:
    with pytest.raises(ValueError, match="Invalid annotation spelling"):
        AnnotationNormalizer().spelling_key(spelling)


def test_annotation_normalization_never_evaluates_expressions() -> None:
    expression = ast.parse("unknown_function()", mode="eval").body
    assert AnnotationNormalizer().key(expression).startswith("Call(")
