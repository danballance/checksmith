"""A single configured check, and the interface every kind of check satisfies."""

from typing import Protocol

from pydantic import BaseModel, ConfigDict

from checksmith.dtos import ToolResult
from checksmith.tools.process_spec import ProcessSpec


class Tool(Protocol):
    """One check that can run itself and say what it found.

    ``name`` is a read-only property rather than a plain attribute, because the
    implementations are frozen. Declaring it as an attribute would ask for
    something writable, and a type checker would then certify an assignment that
    raises at runtime. A plain attribute satisfies a property member, so this is
    the weaker requirement and the honest one.
    """

    @property
    def name(self) -> str:
        """The check's id, as the config file declares it."""
        ...

    def run(self) -> ToolResult:
        """Run the check and report its outcome."""
        ...


class ProcessTool(BaseModel):
    """A check performed by running one process.

    The tool *has* a process specification rather than being one, which keeps
    specification building a pure function and leaves the execution-time
    concerns somewhere obvious.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    """The check's id, as the config file declares it."""

    spec: ProcessSpec
    """Argument vector and working directory for this check."""

    def run(self) -> ToolResult:
        """Run the process and report what it found.

        Not implemented yet. Returning a passing :class:`ToolResult` would be
        worse than failing: it would report success for a check that never ran.
        """
        raise NotImplementedError(
            f"Check execution is not implemented yet; cannot run check: {self.name}"
        )
