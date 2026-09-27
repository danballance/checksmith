import json
from pathlib import Path

import pytest
from pydantic import JsonValue, ValidationError

from astcheck.domain.configuration import AnalysisConfiguration, AnalysisPolicy

VALID_CONFIGURATION = {
    "schema_version": 1,
    "project_root": ".",
    "sources": ["src"],
    "exclusions": [],
    "plugins": [{"id": "sample", "settings": {"limit": 3}}],
}


def test_configuration_preserves_explicit_plugin_settings() -> None:
    configuration = AnalysisConfiguration.model_validate(VALID_CONFIGURATION)

    assert configuration.project_root == Path(".")
    assert configuration.sources == ("src",)
    assert configuration.exclusions == ()
    assert configuration.plugins[0].settings == {"limit": 3}


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", True),
        ("schema_version", "1"),
        ("schema_version", 1.0),
        ("schema_version", 2),
        ("project_root", None),
        ("sources", []),
        ("sources", [" "]),
        ("sources", [1]),
        ("sources", ["src", "src"]),
        ("sources", ["/tmp/src"]),
        ("sources", ["src/../other"]),
        ("exclusions", [" "]),
        ("exclusions", ["../outside"]),
        ("exclusions", ["/absolute/**"]),
        ("exclusions", ["tests", "tests"]),
        ("plugins", []),
        ("plugins", [{"id": "", "settings": {}}]),
        ("plugins", [{"id": "sample", "settings": []}]),
        ("plugins", [{"id": "sample", "settings": {}, "extra": True}]),
        (
            "plugins",
            [{"id": "sample", "settings": {}}, {"id": "sample", "settings": {}}],
        ),
    ],
)
def test_invalid_configuration_values_are_rejected(field: str, value: JsonValue) -> None:
    document = {**VALID_CONFIGURATION, field: value}

    with pytest.raises(ValidationError):
        AnalysisConfiguration.model_validate_json(json.dumps(document))


@pytest.mark.parametrize("field", list(VALID_CONFIGURATION))
def test_configuration_requires_every_field_explicitly(field: str) -> None:
    document = {key: value for key, value in VALID_CONFIGURATION.items() if key != field}

    with pytest.raises(ValidationError, match="Field required"):
        AnalysisConfiguration.model_validate(document)


def test_policy_rejects_configuration_envelope_fields() -> None:
    with pytest.raises(ValidationError, match="Extra inputs"):
        AnalysisPolicy.model_validate(VALID_CONFIGURATION)


def test_policy_is_frozen_and_rejects_unknown_fields() -> None:
    policy = AnalysisPolicy(
        sources=(".",),
        exclusions=(),
        plugins=AnalysisConfiguration.model_validate(VALID_CONFIGURATION).plugins,
    )

    with pytest.raises(ValidationError, match="frozen"):
        setattr(policy, "sources", ("other",))  # noqa: B010
    with pytest.raises(ValidationError, match="Extra inputs"):
        AnalysisPolicy.model_validate({**policy.model_dump(), "unexpected": 1})
