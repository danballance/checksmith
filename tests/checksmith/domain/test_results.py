"""Tests for :mod:`checksmith.domain.results`."""

import pytest
from pydantic import ValidationError

from checksmith.domain.models import ExitCode
from checksmith.domain.results import CliOutput


class Complete(CliOutput):
    """Minimal fully implemented output, used as a stand-in for a real one."""

    detail: str

    @property
    def exit_code(self) -> ExitCode:
        return ExitCode.SUCCESS


def test_a_complete_subclass_can_be_instantiated() -> None:
    output = Complete(detail="done")

    assert output.exit_code is ExitCode.SUCCESS


def test_outputs_are_frozen() -> None:
    output = Complete(detail="done")

    for field in Complete.model_fields:
        with pytest.raises(ValidationError):
            setattr(output, field, "changed")


def test_outputs_serialise_to_json() -> None:
    assert Complete(detail="done").model_dump_json() == '{"detail":"done"}'


def test_results_do_not_need_a_renderer() -> None:
    assert not hasattr(Complete(detail="done"), "__rich__")


def test_a_subclass_without_an_exit_code_cannot_be_instantiated() -> None:
    class NoExitCode(CliOutput):
        pass

    with pytest.raises(TypeError, match="exit_code"):
        NoExitCode()
