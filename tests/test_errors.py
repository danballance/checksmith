"""Tests for :mod:`checksmith.errors`."""

from pathlib import Path

import pytest

from checksmith.dtos import CommandName
from checksmith.errors import (
    CheckExecutionError,
    CheckOutputError,
    ChecksmithError,
    ConfigSchemaError,
    ConfigSyntaxError,
    ConfigurationError,
    DiagnosticContext,
    SchemaViolation,
)

CONFIG_PATH = Path("/workspace/project/.checksmith/checksmith.yaml")
PROJECT_ROOT = Path("/workspace/project")


def execution_error() -> CheckExecutionError:
    """A check whose program was not on the machine that asked for it."""
    return CheckExecutionError(
        check_id="lint",
        program="uvx",
        working_directory=PROJECT_ROOT,
        problem="[Errno 2] No such file or directory: 'uvx'",
    )


def output_error() -> CheckOutputError:
    """A check whose tool ran and then said something unreadable."""
    return CheckOutputError(
        check_id="lint",
        command=CommandName.RUFF,
        summary="ruff exited 2 rather than reporting findings",
        problem="ruff failed\n  Cause: unknown field `nonsense_key`",
    )


def context(*, check_id: str | None) -> DiagnosticContext:
    return DiagnosticContext(config_path=CONFIG_PATH, check_id=check_id)


def test_a_full_diagnostic_reads_as_the_contract_describes() -> None:
    rendered = context(check_id="ruff").render(
        summary="an argument contains a malformed file reference",
        detail="--config={files.}",
        notes=("expected a token of the form {files.NAME}",),
    )

    assert rendered == (
        "Check 'ruff': an argument contains a malformed file reference:\n"
        "--config={files.}\n"
        "  expected a token of the form {files.NAME}\n"
        "  in /workspace/project/.checksmith/checksmith.yaml"
    )


def test_a_diagnostic_without_a_check_has_no_check_prefix() -> None:
    rendered = context(check_id=None).render(
        summary="The config file is empty",
        detail=None,
        notes=(),
    )

    assert rendered == (
        "The config file is empty\n  in /workspace/project/.checksmith/checksmith.yaml"
    )


def test_a_summary_gains_a_colon_only_when_a_detail_follows() -> None:
    without = context(check_id=None).render(
        summary="Something went wrong",
        detail=None,
        notes=(),
    )
    with_detail = context(check_id=None).render(
        summary="Something went wrong",
        detail="here",
        notes=(),
    )

    assert without.startswith("Something went wrong\n")
    assert with_detail.startswith("Something went wrong:\nhere\n")


def test_notes_are_indented_under_the_summary() -> None:
    rendered = context(check_id=None).render(
        summary="The config file has a duplicate mapping key",
        detail=None,
        notes=("checks.0.id", "checks.1.id"),
    )

    assert rendered.startswith(
        "The config file has a duplicate mapping key\n  checks.0.id\n  checks.1.id\n"
    )


def test_every_diagnostic_names_the_config_it_came_from() -> None:
    """There is no error before a config file is named, so the line is never absent."""
    rendered = context(check_id=None).render(
        summary="The config file is empty",
        detail=None,
        notes=(),
    )

    assert rendered.endswith(f"  in {CONFIG_PATH}")


def test_a_syntax_error_is_exactly_the_message_it_was_given() -> None:
    """It is PyYAML's text or the OS's; rewording or decorating it adds nothing."""
    error = ConfigSyntaxError(
        config_path=CONFIG_PATH,
        problem=f'expected a mapping at the top level in "{CONFIG_PATH}", found list',
    )

    assert str(error) == (
        'expected a mapping at the top level in '
        '"/workspace/project/.checksmith/checksmith.yaml", found list'
    )
    assert error.config_path == CONFIG_PATH


def test_a_schema_error_renders_one_line_per_violation() -> None:
    error = ConfigSchemaError(
        config_path=CONFIG_PATH,
        violations=(
            SchemaViolation(
                field="schema_version",
                message="Input should be a valid integer",
            ),
            SchemaViolation(field="checks.0.id", message="Field required"),
        ),
    )

    assert str(error) == (
        "The config file does not match the Checksmith schema\n"
        "  schema_version: Input should be a valid integer\n"
        "  checks.0.id: Field required\n"
        "  in /workspace/project/.checksmith/checksmith.yaml"
    )


def test_a_schema_error_with_one_violation_renders_one_line() -> None:
    error = ConfigSchemaError(
        config_path=CONFIG_PATH,
        violations=(SchemaViolation(field="checks", message="Field required"),),
    )

    assert str(error).count("\n") == 2


@pytest.mark.parametrize(
    "error",
    [
        ConfigSyntaxError(config_path=CONFIG_PATH, problem="unreadable"),
        ConfigSchemaError(config_path=CONFIG_PATH, violations=()),
    ],
)
def test_every_configuration_problem_is_a_checksmith_error(
    error: ChecksmithError,
) -> None:
    """The CLI's error boundary recognises exactly this base class.

    Not ``ConfigurationError``: a config file that could not be read is outside that
    hierarchy, because its message is not one Checksmith wrote.
    """
    assert isinstance(error, ChecksmithError)
    assert str(error) != ""


def test_only_the_errors_checksmith_words_itself_are_configuration_errors() -> None:
    """``ConfigurationError`` means "rendered through ``DiagnosticContext``".

    A syntax error is not: PyYAML and the OS word those, and their text already
    carries the path that the shared renderer would append.
    """
    assert not isinstance(
        ConfigSyntaxError(config_path=CONFIG_PATH, problem="unreadable"),
        ConfigurationError,
    )
    assert isinstance(
        ConfigSchemaError(config_path=CONFIG_PATH, violations=()),
        ConfigurationError,
    )
    assert not isinstance(execution_error(), ConfigurationError)
    assert not isinstance(output_error(), ConfigurationError)


def test_an_execution_error_says_which_check_wanted_which_program_where() -> None:
    """The operating system can name the program; only Checksmith names the check."""
    assert str(execution_error()) == (
        "Check 'lint': could not run uvx in /workspace/project: "
        "[Errno 2] No such file or directory: 'uvx'"
    )


def test_an_execution_error_reaches_the_clis_error_boundary() -> None:
    """A :class:`ChecksmithError`, so it prints as a diagnostic, not a crash."""
    assert isinstance(execution_error(), ChecksmithError)


def test_an_output_error_says_which_check_could_not_be_read_and_why() -> None:
    """The tool's own words, under the id of the check that ran it."""
    assert str(output_error()) == (
        "Check 'lint': ruff exited 2 rather than reporting findings:\n"
        "ruff failed\n"
        "  Cause: unknown field `nonsense_key`"
    )


def test_an_output_error_reaches_the_clis_error_boundary() -> None:
    """A :class:`ChecksmithError`, so it prints as a diagnostic, not a crash."""
    assert isinstance(output_error(), ChecksmithError)


def test_an_output_error_is_not_an_execution_error() -> None:
    """A process that ran and one that never started are separate diagnoses."""
    assert not isinstance(output_error(), CheckExecutionError)
    assert not isinstance(execution_error(), CheckOutputError)
