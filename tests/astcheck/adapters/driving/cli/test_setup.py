import pytest
from typer.testing import CliRunner

from astcheck.adapters.driving.cli.setup import SetupPrompts, TerminalInput
from astcheck.domain.errors import AstcheckError


class ConfiguredTerminal:
    def __init__(self, *, interactive: bool) -> None:
        self._interactive = interactive

    def is_interactive(self) -> bool:
        return self._interactive


@pytest.fixture
def prompts() -> SetupPrompts:
    return SetupPrompts(terminal=ConfiguredTerminal(interactive=True))


def test_setup_preserves_explicit_empty_lists(prompts: SetupPrompts) -> None:
    with CliRunner().isolation(input="application\n\n\n\n"):
        policy = prompts.prompt()

    assert policy.sources == ("application",)
    assert policy.exclusions == ()
    assert policy.plugins[0].settings == {
        "max_standalone_percent": 25,
        "min_callable_statements": 20,
        "min_shared_functions": 3,
        "collaborators": [],
    }


def test_setup_collects_collaborator_spelling_groups(prompts: SetupPrompts) -> None:
    answers = "src\n\n**/tests/**\n\nrepository\nRepository\nRepo\n\n\n"
    with CliRunner().isolation(input=answers):
        policy = prompts.prompt()

    assert policy.sources == ("src",)
    assert policy.exclusions == ("**/tests/**",)
    assert policy.plugins[0].settings["collaborators"] == [
        {"name": "repository", "annotations": ["Repository", "Repo"]}
    ]


@pytest.mark.parametrize(
    "answers",
    [
        "\n\n\n",
        "../src\n\n\n\n",
        "/src\n\n\n\n",
        "src\n\n\nrepository\n\n",
    ],
)
def test_invalid_interactive_answers_fail_without_retrying(
    prompts: SetupPrompts, answers: str
) -> None:
    with (
        CliRunner().isolation(input=answers),
        pytest.raises(AstcheckError, match="Invalid ASTcheck setup"),
    ):
        prompts.prompt()


def test_interactive_setup_requires_terminal_input() -> None:
    prompts = SetupPrompts(terminal=ConfiguredTerminal(interactive=False))
    with pytest.raises(AstcheckError, match="requires terminal input"):
        prompts.prompt()


def test_terminal_input_reads_the_current_stdin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    assert TerminalInput().is_interactive()
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert not TerminalInput().is_interactive()
