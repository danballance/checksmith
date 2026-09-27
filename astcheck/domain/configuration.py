from pathlib import Path, PurePosixPath
from typing import Annotated, Literal, Self

from pydantic import Field, JsonValue, field_validator, model_validator

from astcheck.domain.models import Text, ValueModel


class PluginConfiguration(ValueModel):
    id: Text
    settings: dict[str, JsonValue]


class AnalysisPolicy(ValueModel):
    sources: Annotated[tuple[Text, ...], Field(min_length=1)]
    exclusions: tuple[Text, ...]
    plugins: Annotated[tuple[PluginConfiguration, ...], Field(min_length=1)]

    @field_validator("sources", "exclusions")
    @classmethod
    def _relative_paths(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        for value in values:
            if not value.strip():
                raise ValueError("paths and patterns must not be blank")
            path = PurePosixPath(value)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError(
                    f"paths and patterns must stay relative to project_root: {value!r}"
                )
        if len(set(values)) != len(values):
            raise ValueError("paths and patterns must be unique")
        return values

    @model_validator(mode="after")
    def _unique_plugins(self) -> Self:
        ids = [plugin.id for plugin in self.plugins]
        if len(set(ids)) != len(ids):
            raise ValueError("configured plugin IDs must be unique")
        return self


class AnalysisConfiguration(AnalysisPolicy):
    schema_version: Literal[1]
    project_root: Path

    @field_validator("schema_version", mode="before")
    @classmethod
    def _integer_version(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("schema_version must be an integer")
        return value
