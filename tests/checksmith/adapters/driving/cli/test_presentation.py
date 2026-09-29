import json
from io import StringIO

import pytest
import typer
from rich.console import Console

from checksmith.adapters.driving.cli.presentation import (
    ConsoleProcessOutput,
    OutputFormat,
    OutputPresenter,
)
from checksmith.domain.models import CheckResult, CheckStatus, ExitCode
from checksmith.domain.results import CheckOutput


def test_emit_renders_json_when_asked(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(typer.Exit):
        OutputPresenter(console=Console()).emit(CheckOutput(), OutputFormat.JSON)

    assert json.loads(capsys.readouterr().out) == {"results": []}


def test_emit_renders_a_table_by_default(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(typer.Exit):
        OutputPresenter(console=Console()).emit(
            CheckOutput(
                results=(
                    CheckResult(
                        check_id="ruff", status=CheckStatus.PASSED, messages=()
                    ),
                )
            ),
            OutputFormat.TEXT,
        )

    captured = capsys.readouterr().out
    assert "Checksmith" in captured
    assert "ruff" in captured


def test_emit_exits_with_the_code_its_output_implies(
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = CheckOutput(
        results=(
            CheckResult(
                check_id="ruff",
                status=CheckStatus.FAILED,
                messages=("app.py:1:1 F401 Unused import",),
            ),
        )
    )

    with pytest.raises(typer.Exit) as raised:
        OutputPresenter(console=Console()).emit(output, OutputFormat.TEXT)

    assert raised.value.exit_code == ExitCode.UNHEALTHY


class RecordingStream(StringIO):
    def __init__(self) -> None:
        super().__init__()
        self.flushes = 0

    def flush(self) -> None:
        self.flushes += 1
        super().flush()


def test_process_output_preserves_raw_text_and_flushes_every_chunk() -> None:
    stream = RecordingStream()
    output = ConsoleProcessOutput(console=Console(file=stream, force_terminal=True))
    chunks = ("[bold]first", "\r\x1b[31msecond\x1b[0m", "\n")

    for index, chunk in enumerate(chunks, start=1):
        output.write(chunk)
        assert stream.getvalue() == "".join(chunks[:index])
        assert stream.flushes == index
    output.finish()

    assert stream.flushes == len(chunks)
    assert stream.getvalue() == "".join(chunks)


@pytest.mark.parametrize(
    ("text", "expected", "flushes"),
    [
        ("", "", 0),
        ("done\n", "done\n", 1),
        ("done", "done\n", 2),
        ("done\r", "done\r\n", 2),
    ],
)
def test_process_output_finishes_only_partial_lines(
    text: str, expected: str, flushes: int
) -> None:
    stream = RecordingStream()
    output = ConsoleProcessOutput(console=Console(file=stream))

    output.write(text)
    output.write("")
    output.finish()
    output.finish()

    assert stream.getvalue() == expected
    assert stream.flushes == flushes


def test_presenter_creates_independent_process_output_for_each_invocation() -> None:
    stream = RecordingStream()
    presenter = OutputPresenter(console=Console(file=stream))
    first = presenter.process_output()
    first.write("partial")

    second = presenter.process_output()
    second.finish()

    assert stream.getvalue() == "partial"
    assert stream.flushes == 1
    first.finish()
    assert stream.getvalue() == "partial\n"
