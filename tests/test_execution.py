"""Tests for :mod:`checksmith.execution`."""

from pathlib import Path

import pytest

from checksmith.dtos import ExitCode, ToolResult
from checksmith.execution import CheckRunner, Runner
from checksmith.tools.process_spec import ProcessSpec
from checksmith.tools.tool import ProcessTool, Tool


class RecordingTool:
    """A tool that is not a ``ProcessTool``, to show the runner only needs the
    interface.

    ``name`` is a property here and a plain field on ``ProcessTool``; both
    satisfy :class:`Tool`, which is why it declares a property.
    """

    def __init__(self, *, name: str, result: ToolResult) -> None:
        self._name = name
        self._result = result
        self.runs = 0

    @property
    def name(self) -> str:
        return self._name

    def run(self) -> ToolResult:
        self.runs += 1
        return self._result


@pytest.fixture
def tool() -> ProcessTool:
    return ProcessTool(
        name="ruff",
        spec=ProcessSpec(
            argv=("uvx", "--from", "ruff==0.16.7", "ruff", "check", "."),
            working_directory=Path("/workspace/project"),
        ),
    )


def test_runner_retains_the_tools_it_was_given(tool: ProcessTool) -> None:
    tools: tuple[Tool, ...] = (tool,)

    runner = Runner(tools=tools)

    assert runner.tools == tools


def test_a_runner_is_a_check_runner(tool: ProcessTool) -> None:
    """Checked statically: the annotation is the assertion."""
    runner: CheckRunner = Runner(tools=(tool,))

    assert runner is not None


def test_every_tool_is_run_once_and_every_result_is_kept() -> None:
    recorders = (
        RecordingTool(name="ruff", result=ToolResult(name="ruff")),
        RecordingTool(name="ty", result=ToolResult(name="ty", failed=True)),
    )
    tools: tuple[Tool, ...] = recorders

    output = Runner(tools=tools).check()

    assert tuple(result.name for result in output.results) == ("ruff", "ty")
    assert [recorder.runs for recorder in recorders] == [1, 1]
    assert output.exit_code is ExitCode.UNHEALTHY


def test_a_tool_that_cannot_run_stops_the_whole_run(tool: ProcessTool) -> None:
    """Execution is unimplemented, so this is how a real suite behaves today."""
    with pytest.raises(NotImplementedError) as raised:
        Runner(tools=(tool,)).check()

    assert "ruff" in str(raised.value)


def test_a_run_with_no_tools_at_all_is_rejected() -> None:
    """The schema forbids an empty suite, so reaching here is a caller's bug."""
    with pytest.raises(ValueError, match="at least one tool"):
        Runner(tools=()).check()
