from io import StringIO

import pytest

from checksmith.adapters.driving.cli.setup import TerminalInput


def test_terminal_input_rejects_redirected_input(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stdin", StringIO("policy input"))

    assert not TerminalInput().is_interactive()
