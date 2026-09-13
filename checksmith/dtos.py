"""Process exit codes defined by the Checksmith CLI schema.

Lives outside ``checksmith.cli`` so that output models can reference it without
importing the CLI module, which imports them.
"""

from enum import IntEnum


class ExitCode(IntEnum):
    """Process exit codes defined by the Checksmith CLI schema."""

    SUCCESS = 0
    """The operation completed successfully."""

    UNHEALTHY = 1
    """The operation completed but found a failing or unhealthy state."""

    ERROR = 2
    """Checksmith could not complete the operation."""
