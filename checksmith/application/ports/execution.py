from collections.abc import Mapping
from pathlib import Path
from typing import Literal, Protocol

from checksmith.domain.config import Check
from checksmith.domain.models import CheckResult, CommandName

type StreamName = Literal["stdout", "stderr"]


class ProcessOutput(Protocol):
    def write(self, text: str) -> None: ...


class CheckExecutor(Protocol):
    def check_is_runnable(self, *, check: Check, project_root: Path) -> bool: ...

    def run(
        self, *, check: Check, project_root: Path, output: ProcessOutput | None
    ) -> CheckResult: ...


class CommandRegistry(Protocol):
    def registry(self) -> Mapping[CommandName, CheckExecutor]: ...
