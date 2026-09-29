from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

Text = Annotated[StrictStr, Field(min_length=1)]
Position = Annotated[StrictInt, Field(ge=1)]


class ValueModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class SourceLocation(ValueModel):
    path: Text
    line: Position
    column: Position


class Finding(ValueModel):
    rule_id: Text
    message: Text
    location: SourceLocation
    related_locations: tuple[SourceLocation, ...]


class PluginReport(ValueModel):
    plugin_id: Text
    findings: tuple[Finding, ...]


class AnalysisError(ValueModel):
    message: Text
    path: Text | None
    line: Position | None
    column: Position | None


class AnalysisReport(ValueModel):
    schema_version: Literal[1]
    status: Literal["complete", "error"]
    modules: tuple[Text, ...]
    plugins: tuple[PluginReport, ...]
    errors: tuple[AnalysisError, ...]

    @field_validator("schema_version", mode="before")
    @classmethod
    def _integer_version(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("schema_version must be an integer")
        return value

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.status == "complete":
            if self.errors or not self.modules or not self.plugins:
                raise ValueError(
                    "complete reports require modules and plugins, and no errors"
                )
        elif not self.errors:
            raise ValueError("error reports require at least one error")
        if len(set(self.modules)) != len(self.modules):
            raise ValueError("report module paths must be unique")
        ids = [plugin.plugin_id for plugin in self.plugins]
        if len(set(ids)) != len(ids):
            raise ValueError("report plugin IDs must be unique")
        paths = set(self.modules)
        for plugin in self.plugins:
            for finding in plugin.findings:
                for location in (finding.location, *finding.related_locations):
                    if location.path not in paths:
                        raise ValueError(
                            f"finding references unanalyzed source {location.path!r}"
                        )
        return self

    @property
    def exit_code(self) -> int:
        if self.status == "error":
            return 2
        return int(any(plugin.findings for plugin in self.plugins))
