"""Values shared between the layers, owned by none of them.

These live here so that a module can reference one without importing a module
that imports it back: ``ExitCode`` is used by the output models and by the CLI
that renders them, and ``ToolResult`` is produced by a tool and consumed by the
output that displays it.
"""

from enum import IntEnum

from pydantic import BaseModel


class ExitCode(IntEnum):
    """Process exit codes defined by the Checksmith CLI schema."""

    SUCCESS = 0
    """The operation completed successfully."""

    UNHEALTHY = 1
    """The operation completed but found a failing or unhealthy state."""

    ERROR = 2
    """Checksmith could not complete the operation."""


class ToolResult(BaseModel):
    """Outcome of running a single configured tool."""

    name: str
    """Name of the tool that was run."""

    failed: bool = False
    """Whether the tool reported a coding standard violation."""

    findings: tuple[str, ...] = ()
    """Human-readable descriptions of what the tool reported."""
