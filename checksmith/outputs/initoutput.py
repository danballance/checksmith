from pathlib import Path

from rich.console import RenderableType
from rich.text import Text

from checksmith.dtos import ExitCode
from checksmith.outputs.base import CliOutput


class InitOutput(CliOutput):
    project_root: Path
    created_files: tuple[Path, ...]

    @property
    def exit_code(self) -> ExitCode:
        return ExitCode.SUCCESS

    def __rich__(self) -> RenderableType:
        return Text(
            f"Project root: {self.project_root}\nCreated:\n"
            + "\n".join(f"  {path}" for path in self.created_files)
        )
