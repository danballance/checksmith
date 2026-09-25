from importlib.resources import files
from pathlib import Path

from checksmith.commands.command import Command
from checksmith.config import Check
from checksmith.dtos import CheckResult, CheckStatus, CommandName
from checksmith.errors import CheckOutputError
from checksmith.packages import UvPackage


class PytestCommand(Command):
    @property
    def name(self) -> CommandName:
        return CommandName.PYTEST

    def run(self, *, check: Check, project_root: Path) -> CheckResult:
        source = (files("checksmith.commands") / "_pytest_launcher.py").read_text(
            encoding="utf-8"
        )
        argv = UvPackage().build_argv(
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
