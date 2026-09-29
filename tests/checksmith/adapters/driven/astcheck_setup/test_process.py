import json
import os
import pty
import subprocess
import sys
from pathlib import Path

import pytest

from checksmith.adapters.driven.astcheck_setup.process import TerminalSetupProcess
from checksmith.domain.errors import ChecksmithError


def test_policy_process_closes_input_captures_stdout_and_inherits_stderr(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    script = (
        "import json, os, sys; "
        "print('diagnostic', file=sys.stderr); "
        "print(json.dumps({'cwd': os.getcwd(), 'stdin': sys.stdin.read()}))"
    )
    result = TerminalSetupProcess().run(
        argv=(sys.executable, "-c", script), cwd=tmp_path, interactive=False
    )
    assert result.returncode == 0
    assert json.loads(result.stdout) == {"cwd": str(tmp_path), "stdin": ""}
    captured = capfd.readouterr()
    assert captured.out == ""
    assert captured.err == "diagnostic\n"


def test_interactive_process_requires_terminal_before_spawn(tmp_path: Path) -> None:
    with pytest.raises(ChecksmithError, match="requires terminal input"):
        TerminalSetupProcess().run(argv=("never-run",), cwd=tmp_path, interactive=True)


def test_process_preserves_nonzero_exit_status(tmp_path: Path) -> None:
    result = TerminalSetupProcess().run(
        argv=(sys.executable, "-c", "raise SystemExit(2)"),
        cwd=tmp_path,
        interactive=False,
    )
    assert result.returncode == 2
    assert result.stdout == b""


def test_process_propagates_spawn_failure(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        TerminalSetupProcess().run(
            argv=(str(tmp_path / "missing-command"),), cwd=tmp_path, interactive=False
        )


def test_interactive_subprocess_inherits_actual_terminal_and_captures_only_stdout(
    tmp_path: Path,
) -> None:
    child_script = (
        "import json, sys; "
        "print('setup prompt', file=sys.stderr, flush=True); "
        "answer = input(); "
        "print(json.dumps({'terminal': sys.stdin.isatty(), 'answer': answer}))"
    )
    runner_script = (
        "import sys; from pathlib import Path; "
        "from checksmith.adapters.driven.astcheck_setup.process import "
        "TerminalSetupProcess; "
        "result = TerminalSetupProcess().run("
        f"argv=(sys.executable, '-c', {child_script!r}), "
        f"cwd=Path({str(tmp_path)!r}), interactive=True); "
        "sys.stdout.buffer.write(result.stdout); raise SystemExit(result.returncode)"
    )
    master, slave = pty.openpty()
    try:
        child = subprocess.Popen(
            (sys.executable, "-c", runner_script),
            stdin=slave,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        os.write(master, b"terminal answer\n")
        stdout, stderr = child.communicate(timeout=10)
        assert child.returncode == 0
        assert json.loads(stdout) == {"terminal": True, "answer": "terminal answer"}
        assert stderr == b"setup prompt\n"
    finally:
        os.close(master)
        os.close(slave)
