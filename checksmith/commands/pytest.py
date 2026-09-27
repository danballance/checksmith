from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Protocol

from checksmith.commands.command import Command
from checksmith.config import Check
from checksmith.dtos import CheckResult, CheckStatus, CommandName
from checksmith.errors import CheckOutputError
from checksmith.packages import Package
from checksmith.prerequisites import UvPrerequisites
from checksmith.processes import ProcessExecutor


class LauncherSource(Protocol):
    def read(self) -> str: ...


class PackagedLauncherSource:
    def __init__(self, resource: Traversable) -> None:
        self._resource = resource

    def read(self) -> str:
        return self._resource.read_text(encoding="utf-8")


class PytestCommand(Command):
    def __init__(
        self,
        executor: ProcessExecutor,
        uv_prerequisites: UvPrerequisites,
        source: LauncherSource,
        package: Package,
    ) -> None:
        super().__init__(executor=executor, uv_prerequisites=uv_prerequisites)
        self._source = source
        self._package = package

    @property
    def name(self) -> CommandName:
        return CommandName.PYTEST

    def run(self, *, check: Check, project_root: Path) -> CheckResult:
        source = self._source.read()
        argv = self._package.build_argv(
            package=check.package,
            command="python",
            arguments=("-c", source, *check.arguments),
        )
        return self._run_argv(check=check, project_root=project_root, argv=argv)

    def process_response(
        self,
        *,
        check_id: str,
        project_root: Path,
        exit_code: int,
        stdout: str,
        stderr: str,
    ) -> CheckResult:
        messages = tuple(
            output.strip() for output in (stdout, stderr) if output.strip()
        )
        # The standalone launcher reserves 10..16 for completed pytest calls.
        # uv failures and Python import failures keep their original exit codes.
        match exit_code:
            case 10:
                status = CheckStatus.PASSED
            case 15:
                status = CheckStatus.PASSED
                messages += ("No tests were collected; the check passes.",)
            case 11 | 16:
                status = CheckStatus.FAILED
            case _:
                match exit_code:
                    case 12:
                        summary = (
                            "pytest could not complete collection or was interrupted"
                        )
                    case 13:
                        summary = "pytest encountered an internal error"
                    case 14:
                        summary = "pytest rejected its configuration or arguments"
                    case _:
                        summary = (
                            f"pytest launcher exited {exit_code} without returning "
                            "a recognized pytest result"
                        )
                raise CheckOutputError(
                    check_id=check_id,
                    command=self.name,
                    summary=summary,
                    problem="\n".join(messages),
                )
        return CheckResult(check_id=check_id, status=status, messages=messages)
