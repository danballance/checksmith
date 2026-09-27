from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from astcheck.plugins.oopcheck.annotations import AnnotationNormalizer


class CollaboratorGroup(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    name: str = Field(min_length=1)
    annotations: tuple[str, ...] = Field(min_length=1, strict=False)

    @field_validator("name")
    @classmethod
    def validate_name(cls, name: str) -> str:
        if not name.strip():
            raise ValueError("Collaborator names must contain non-whitespace characters")
        return name

    @field_validator("annotations")
    @classmethod
    def validate_annotations(cls, annotations: tuple[str, ...]) -> tuple[str, ...]:
        normalizer = AnnotationNormalizer()
        seen: set[str] = set()
        for spelling in annotations:
            key = normalizer.spelling_key(spelling)
            if key in seen:
                raise ValueError(f"Duplicate annotation spelling {spelling!r}")
            seen.add(key)
        return annotations


class OopcheckSettings(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    max_standalone_percent: int = Field(ge=0, le=100)
    min_callable_statements: int = Field(ge=1)
    min_shared_functions: int = Field(ge=2)
    collaborators: tuple[CollaboratorGroup, ...] = Field(strict=False)

    @model_validator(mode="after")
    def validate_collaborators(self) -> Self:
        normalizer = AnnotationNormalizer()
        names: set[str] = set()
        owners: dict[str, str] = {}
        for group in self.collaborators:
            if group.name in names:
                raise ValueError(f"Duplicate collaborator name {group.name!r}")
            names.add(group.name)
            for spelling in group.annotations:
                key = normalizer.spelling_key(spelling)
                if key in owners:
                    raise ValueError(
                        f"Annotation {spelling!r} belongs to both "
                        f"{owners[key]!r} and {group.name!r}"
                    )
                owners[key] = group.name
        return self
