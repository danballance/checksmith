import pytest
from pydantic import JsonValue, ValidationError

from astcheck.domain.oopcheck.settings import OopcheckSettings


@pytest.fixture
def settings() -> dict[str, JsonValue]:
    return {
        "max_standalone_percent": 25,
        "min_callable_statements": 20,
        "min_shared_functions": 3,
        "collaborators": [
            {"name": "database", "annotations": ["Session", "DatabaseSession"]}
        ],
    }


def test_json_settings_produce_typed_immutable_models(
    settings: dict[str, JsonValue],
) -> None:
    configured = OopcheckSettings.model_validate(settings)
    assert configured.max_standalone_percent == 25
    assert configured.collaborators[0].annotations == ("Session", "DatabaseSession")
    field = "max_standalone_percent"
    with pytest.raises(ValidationError, match="frozen"):
        setattr(configured, field, 50)


@pytest.mark.parametrize(
    "field",
    [
        "max_standalone_percent",
        "min_callable_statements",
        "min_shared_functions",
        "collaborators",
    ],
)
def test_every_setting_is_required(settings: dict[str, JsonValue], field: str) -> None:
    del settings[field]
    with pytest.raises(ValidationError, match="Field required"):
        OopcheckSettings.model_validate(settings)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("max_standalone_percent", -1),
        ("max_standalone_percent", 101),
        ("max_standalone_percent", "25"),
        ("max_standalone_percent", True),
        ("min_callable_statements", 0),
        ("min_callable_statements", 1.5),
        ("min_shared_functions", 1),
        ("min_shared_functions", False),
        ("collaborators", None),
        ("unknown", 1),
    ],
)
def test_invalid_or_unknown_settings_fail(
    settings: dict[str, JsonValue], field: str, value: JsonValue
) -> None:
    settings[field] = value
    with pytest.raises(ValidationError):
        OopcheckSettings.model_validate(settings)


@pytest.mark.parametrize("maximum", [0, 100])
def test_percentage_endpoints_and_empty_collaborators_are_valid(
    settings: dict[str, JsonValue], maximum: int
) -> None:
    settings["max_standalone_percent"] = maximum
    settings["collaborators"] = []
    configured = OopcheckSettings.model_validate(settings)
    assert configured.max_standalone_percent == maximum
    assert configured.collaborators == ()


@pytest.mark.parametrize(
    "group",
    [
        {"name": "", "annotations": ["Session"]},
        {"name": "  ", "annotations": ["Session"]},
        {"name": 1, "annotations": ["Session"]},
        {"name": "database", "annotations": []},
        {"name": "database", "annotations": [1]},
        {"name": "database", "annotations": "Session"},
        {"name": "database", "annotations": ["Session["]},
        {"name": "database", "annotations": ["Session", "'Session'"]},
        {"name": "database", "annotations": ["Session"], "extra": True},
        {"name": "database"},
        {"annotations": ["Session"]},
    ],
)
def test_invalid_collaborator_groups_fail(
    settings: dict[str, JsonValue], group: dict[str, JsonValue]
) -> None:
    settings["collaborators"] = [group]
    with pytest.raises(ValidationError):
        OopcheckSettings.model_validate(settings)


@pytest.mark.parametrize(
    ("second_name", "second_annotation", "message"),
    [
        ("database", "OtherSession", "Duplicate collaborator name"),
        ("other", " Session ", "belongs to both"),
        ("other", "'Session'", "belongs to both"),
    ],
)
def test_duplicate_names_and_ambiguous_annotations_fail(
    settings: dict[str, JsonValue],
    second_name: str,
    second_annotation: str,
    message: str,
) -> None:
    settings["collaborators"] = [
        {"name": "database", "annotations": ["Session"]},
        {"name": second_name, "annotations": [second_annotation]},
    ]
    with pytest.raises(ValidationError, match=message):
        OopcheckSettings.model_validate(settings)
