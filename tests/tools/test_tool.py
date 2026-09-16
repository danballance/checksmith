"""Tests for :mod:`checksmith.tools.tool`."""

from pathlib import Path

import pytest

from checksmith.tools.process_spec import ProcessSpec
from checksmith.tools.tool import ProcessTool, Tool


@pytest.fixture
def spec() -> ProcessSpec:
    return ProcessSpec(
        argv=("uvx", "--from", "ruff==0.16.7", "ruff", "check", "."),
        working_directory=Path("/workspace/project"),
    )


def test_a_tool_pairs_a_name_with_a_process_specification(
    spec: ProcessSpec,
) -> None:
    tool = ProcessTool(name="ruff", spec=spec)

    assert tool.name == "ruff"
    assert tool.spec is spec


def test_a_process_tool_is_a_tool(spec: ProcessSpec) -> None:
    """Checked statically: the annotation is the assertion."""
    tool: Tool = ProcessTool(name="ruff", spec=spec)

    assert tool.name == "ruff"


def test_a_tool_is_frozen(spec: ProcessSpec) -> None:
    tool = ProcessTool(name="ruff", spec=spec)

    for field in ProcessTool.model_fields:
        with pytest.raises(ValueError):
            setattr(tool, field, None)


def test_running_fails_loudly_while_execution_is_unimplemented(
    spec: ProcessSpec,
) -> None:
    """A passing result would report success for a check that never ran."""
    tool = ProcessTool(name="ruff", spec=spec)

    with pytest.raises(NotImplementedError) as raised:
        tool.run()

    assert "ruff" in str(raised.value)
