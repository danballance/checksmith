"""Building the process specification for a configured check.

The result is an argument *vector* and a working directory, never a command
string: nothing here is ever handed to a shell, so a path containing a space or
a quote needs no escaping and gets none.
"""

from pathlib import Path
from typing import Self

from pydantic import BaseModel, ConfigDict

from checksmith.config import Check
from checksmith.runners.registry import runner_for


class ProcessSpec(BaseModel):
    """Exactly how to start one check's process."""

    model_config = ConfigDict(frozen=True)

    argv: tuple[str, ...]
    """Argument vector, including the runner itself at position zero."""

    working_directory: Path
    """Directory the process runs in: the config's resolved project root."""

    @classmethod
    def from_check(cls, *, check: Check, project_root: Path) -> Self:
        """Assemble the runner invocation for one check.

        The project root is a parameter rather than something read off the
        check: it belongs to the config as a whole, and every check a run
        executes shares the one the caller passes here.
        """
        return cls(
            argv=runner_for(name=check.runner).build_argv(
                package=check.package,
                command=check.command,
                arguments=check.args,
            ),
            working_directory=project_root,
        )
