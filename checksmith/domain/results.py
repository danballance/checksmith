from abc import ABC, abstractmethod
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from checksmith.domain.models import CheckResult, CheckStatus, ExitCode


class CliOutput(BaseModel, ABC):
    model_config = ConfigDict(frozen=True)

    @property
    @abstractmethod
    def exit_code(self) -> ExitCode: ...


class CheckOutput(CliOutput):
    results: tuple[CheckResult, ...] = ()

    @property
    def exit_code(self) -> ExitCode:
        if any(result.status is CheckStatus.ERROR for result in self.results):
            return ExitCode.ERROR
        if any(result.status is CheckStatus.FAILED for result in self.results):
            return ExitCode.UNHEALTHY
        return ExitCode.SUCCESS


class InitOutput(CliOutput):
    project_root: Path
    created_files: tuple[Path, ...]

    @property
    def exit_code(self) -> ExitCode:
        return ExitCode.SUCCESS
