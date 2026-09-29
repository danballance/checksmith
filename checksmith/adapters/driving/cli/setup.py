import sys
from typing import Protocol

from checksmith.domain.errors import ChecksmithError


class InteractiveInput(Protocol):
    def is_interactive(self) -> bool: ...


class TerminalInput:
    def is_interactive(self) -> bool:
        return sys.stdin.isatty()


class InitializationInput:
    def __init__(self, terminal: InteractiveInput) -> None:
        self._terminal = terminal

    def require_interactive(self, *, missing_options: tuple[str, ...]) -> None:
        if missing_options and not self._terminal.is_interactive():
            raise ChecksmithError(
                "Noninteractive initialization requires "
                + " and ".join(missing_options)
                + "."
            )
