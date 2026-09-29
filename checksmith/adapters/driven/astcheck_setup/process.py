import subprocess
import sys
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from checksmith.domain.errors import ChecksmithError


class SetupProcessResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    returncode: int
    stdout: bytes


class SetupProcess(Protocol):
    def run(
        self, *, argv: tuple[str, ...], cwd: Path, interactive: bool
    ) -> SetupProcessResult: ...


class TerminalSetupProcess:
    def run(
        self, *, argv: tuple[str, ...], cwd: Path, interactive: bool
    ) -> SetupProcessResult:
        if interactive and not sys.stdin.isatty():
            raise ChecksmithError("Interactive ASTcheck setup requires terminal input.")
        result = subprocess.run(
            argv,
            cwd=cwd,
            stdin=None if interactive else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=None,
            check=False,
        )
        return SetupProcessResult(returncode=result.returncode, stdout=result.stdout)
