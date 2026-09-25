"""Values shared between the layers, owned by none of them.

These live here so that a module can reference one without importing a module
that imports it back: ``ExitCode`` is used by the output models and by the CLI
that renders them, and ``CheckResult`` is produced by a command and consumed by
the output that displays it.
"""

from enum import IntEnum, StrEnum

from pydantic import BaseModel


class ExitCode(IntEnum):
    """Process exit codes defined by the Checksmith CLI schema."""

    SUCCESS = 0
    """The operation completed successfully."""

    UNHEALTHY = 1
    """The operation completed but found a failing or unhealthy state."""

    ERROR = 2
    """Checksmith could not complete the operation."""


class CheckStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"
    SKIPPED = "skipped"


class CheckResult(BaseModel):
    """Outcome of running a single configured check."""

    check_id: str
    """The check's id, as the config file declares it."""

    status: CheckStatus

    messages: tuple[str, ...]


class PackageType(StrEnum):
    """Which ecosystem a check's package comes from."""

    UVX = "uvx"
    """A Python package, run through ``uvx``."""

    NPX = "npx"
    """An npm package, run through ``npx``."""

    UV = "uv"
    """A project-owned Python command, run through ``uv run --locked``."""


class CommandName(StrEnum):
    """Which command a check configures, and so which reads its output."""

    RUFF = "ruff"
    """Ruff, a Python linter and formatter."""

    SEMGREP = "semgrep"

    IMPORT_LINTER = "import-linter"

    TY = "ty"

    PYARCHGRAPH = "pyarchgraph"

    PYTEST = "pytest"
