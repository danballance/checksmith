"""Tests for :mod:`checksmith.outputs.base`."""

import pytest
from pydantic import ValidationError
from rich.console import RenderableType
from rich.text import Text

from checksmith.dtos import ExitCode
from checksmith.outputs.base import CliOutput


class Complete(CliOutput):
    """Minimal fully implemented output, used as a stand-in for a real one."""

    detail: str

    @property
    def exit_code(self) -> ExitCode:
        return ExitCode.SUCCESS

    def __rich__(self) -> RenderableType:
        return Text(self.detail)


def test_a_complete_subclass_can_be_instantiated() -> None:
    output = Complete(detail="done")

    assert output.exit_code is ExitCode.SUCCESS
    assert isinstance(output.__rich__(), Text)


def test_outputs_are_frozen() -> None:
    output = Complete(detail="done")

    with pytest.raises(ValidationError):
        output.detail = "changed"


def test_outputs_serialise_to_json() -> None:
    assert Complete(detail="done").model_dump_json() == '{"detail":"done"}'


def test_a_subclass_without_a_rich_rendering_cannot_be_instantiated() -> None:
    class NoRendering(CliOutput):
        @property
        def exit_code(self) -> ExitCode:
            return ExitCode.SUCCESS

    with pytest.raises(TypeError, match="__rich__"):
        NoRendering()


def test_a_subclass_without_an_exit_code_cannot_be_instantiated() -> None:
    class NoExitCode(CliOutput):
        def __rich__(self) -> RenderableType:
            return Text("")

    with pytest.raises(TypeError, match="exit_code"):
        NoExitCode()
