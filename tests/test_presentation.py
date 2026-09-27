import json

import pytest
import typer
from rich.console import Console

from checksmith.dtos import CheckResult, CheckStatus, ExitCode
from checksmith.outputs.checkoutput import CheckOutput
from checksmith.presentation import OutputFormat, OutputPresenter


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
