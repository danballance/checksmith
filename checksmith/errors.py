"""Errors Checksmith raises deliberately, and how they are rendered.

Every error here renders itself at construction time, so ``str(error)`` is
exactly the text a user reads: the CLI's error boundary prints it verbatim
rather than labelling it with an implementation class name.

A problem Checksmith worked out for itself is a :class:`ConfigurationError`,
which pairs a summary with the field it belongs to. A config file that could not be
read is not, and neither is a check whose process never started: PyYAML and the
operating system write those diagnostics, and they already say where the trouble
is.

The rendered shape is::

    The config file does not match the Checksmith schema
      checks.0.package: Value error, a uvx package must name a version
      in /workspace/project/.checksmith/checksmith.yaml

A ``Check '<id>': `` prefix and a detail line sit above the notes when the
raising code knows them, and are omitted when it does not. Positions are given
as dotted field paths such as ``checks.0.package``: carrying a line and column
for every field in the document is not worth the precision. Where PyYAML has
already worked a position out for itself, the note quoting it says so in
PyYAML's own words.
"""

from pathlib import Path

from pydantic import BaseModel, ConfigDict


class DiagnosticContext(BaseModel):
    """Everything known about *where* a configuration problem was found.

    ``check_id`` is required so that a raise site states explicitly what it does
    and does not know: ``None`` means genuinely unknown, never forgotten.
    """

    model_config = ConfigDict(frozen=True)

    config_path: Path
    """Config file the problem belongs to. Nothing fails before one is named."""

    check_id: str | None
    """Check the problem belongs to, or ``None`` for config-wide problems."""

    def render(
        self,
        *,
        summary: str,
        detail: str | None,
        notes: tuple[str, ...],
    ) -> str:
        """Assemble the user-facing text for one configuration problem."""
        prefix = "" if self.check_id is None else f"Check '{self.check_id}': "
        lines = (
            [f"{prefix}{summary}"]
            if detail is None
            else [f"{prefix}{summary}:", detail]
        )
        lines.extend(f"  {note}" for note in notes)
        lines.append(f"  in {self.config_path}")
        return "\n".join(lines)


class ChecksmithError(Exception):
    """Base class for every error Checksmith raises on purpose.

    A plain :class:`Exception` rather than a Pydantic model: ``BaseModel``'s
    metaclass and ``BaseException``'s cannot be combined.
    """


class ConfigurationError(ChecksmithError):
    """A problem with the project's Checksmith configuration."""

    def __init__(
        self,
        *,
        context: DiagnosticContext,
        summary: str,
        detail: str | None,
        notes: tuple[str, ...],
    ) -> None:
        self.context = context
        self.summary = summary
        self.detail = detail
        self.notes = notes
        super().__init__(
            context.render(summary=summary, detail=detail, notes=notes)
        )


class ConfigSyntaxError(ChecksmithError):
    """A config file could not be read, or is not YAML with a mapping at its top.

    Deliberately not a :class:`ConfigurationError`. The message comes from
    PyYAML or the operating system and already names the file and, where one is
    known, the line and column; rendering it through :class:`DiagnosticContext`
    would print the path a second time.
    """

    def __init__(self, *, config_path: Path, problem: str) -> None:
        self.config_path = config_path
        self.problem = problem
        super().__init__(problem)


class SchemaViolation(BaseModel):
    """One field-level complaint from schema validation."""

    model_config = ConfigDict(frozen=True)

    field: str
    """Dotted path of the offending field, such as ``checks.0.package_type``."""

    message: str
    """What is wrong with it, in Pydantic's words."""


class ConfigSchemaError(ConfigurationError):
    """A config file parsed as YAML but does not match the Checksmith schema.

    Every violation from a single validation pass is rendered, because they are
    all available at no extra cost and fixing them one round-trip at a time is
    needless work for the user.
    """

    def __init__(
        self,
        *,
        config_path: Path,
        violations: tuple[SchemaViolation, ...],
    ) -> None:
        self.violations = violations
        super().__init__(
            context=DiagnosticContext(config_path=config_path, check_id=None),
            summary="The config file does not match the Checksmith schema",
            detail=None,
            notes=tuple(
                f"{violation.field}: {violation.message}" for violation in violations
            ),
        )


class CheckExecutionError(ChecksmithError):
    """A check's process could not be started.

    Deliberately not a :class:`ConfigurationError`, for both of the reasons
    :class:`ConfigSyntaxError` is not. The wording is the operating system's ---
    an absent ``uvx`` is a machine that is not set up, not a line anybody can
    edit --- and the command that raises this never sees the config file, so
    there is no path for the shared renderer to append.

    What the operating system cannot say is which check wanted the program. One
    config may declare several, and they may share it.
    """

    def __init__(
        self,
        *,
        check_id: str,
        program: str,
        working_directory: Path,
        problem: str,
    ) -> None:
        self.check_id = check_id
        self.program = program
        self.working_directory = working_directory
        self.problem = problem
        super().__init__(
            f"Check '{check_id}': could not run {program} "
            f"in {working_directory}: {problem}"
        )
