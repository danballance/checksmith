from pathlib import Path
from typing import TypedDict

import pytest
import yaml
from typer.testing import CliRunner

from astcheck.domain.errors import AstcheckError
from astcheck.plugins.oopcheck.plugin import OopcheckPlugin
from astcheck.ports import PluginFactory
from checksmith.astcheck_setup import AstcheckSetup, TerminalInput
from checksmith.errors import ChecksmithError


class ConfiguredTerminal:
    def __init__(self, interactive: bool) -> None:
        self._interactive = interactive

    def is_interactive(self) -> bool:
        return self._interactive


class RecordingRegistry:
    def __init__(self, error: AstcheckError | None) -> None:
        self.error = error
        self.ids: list[str] = []

    def load(self, *, plugin_id: str) -> PluginFactory:
        self.ids.append(plugin_id)
        if self.error is not None:
            raise self.error
        return OopcheckPlugin()


class CollaboratorDocument(TypedDict):
    name: str
    annotations: list[str]


class SettingsDocument(TypedDict):
    max_standalone_percent: int
    min_callable_statements: int
    min_shared_functions: int
    collaborators: list[CollaboratorDocument]


class PluginDocument(TypedDict):
    id: str
    settings: SettingsDocument


class PolicyDocument(TypedDict):
    sources: list[str]
    exclusions: list[str]
    plugins: list[PluginDocument]


def policy_document() -> PolicyDocument:
    return {
        "sources": ["src"],
        "exclusions": ["**/tests/**"],
        "plugins": [
            {
                "id": "oopcheck",
                "settings": {
                    "max_standalone_percent": 25,
                    "min_callable_statements": 20,
                    "min_shared_functions": 3,
                    "collaborators": [
                        {"name": "repository", "annotations": ["Repository", "Repo"]}
                    ],
                },
            }
        ],
    }


@pytest.fixture
def registry() -> RecordingRegistry:
    return RecordingRegistry(error=None)


@pytest.fixture
def setup(registry: RecordingRegistry) -> AstcheckSetup:
    return AstcheckSetup(
        registry=registry, terminal=ConfiguredTerminal(interactive=True)
    )


def test_policy_file_keeps_explicit_paths_and_settings(
    setup: AstcheckSetup, registry: RecordingRegistry, tmp_path: Path
) -> None:
    policy_file = tmp_path / "policy.yaml"
    policy_file.write_text(yaml.safe_dump(policy_document()), encoding="utf-8")

    policy = setup.policy(config_file=policy_file)

    assert policy.model_dump(mode="json") == policy_document()
    assert registry.ids == ["oopcheck"]


@pytest.mark.parametrize(
    "content",
    [
        "",
        "[]",
        "sources: [",
        "sources: [src]",
        "sources: [src]\nextra: yes",
        "date: 2026-09-27",
    ],
)
def test_invalid_policy_files_fail_clearly(
    setup: AstcheckSetup, tmp_path: Path, content: str
) -> None:
    policy_file = tmp_path / "policy.yaml"
    policy_file.write_text(content, encoding="utf-8")

    with pytest.raises(ChecksmithError, match="ASTcheck policy"):
        setup.policy(config_file=policy_file)


@pytest.mark.parametrize("content", [None, b"\xff"])
def test_unreadable_policy_files_fail_clearly(
    setup: AstcheckSetup, tmp_path: Path, content: bytes | None
) -> None:
    policy_file = tmp_path / "policy.yaml"
    if content is not None:
        policy_file.write_bytes(content)

    with pytest.raises(ChecksmithError, match="Could not read ASTcheck policy"):
        setup.policy(config_file=policy_file)


def test_unknown_plugin_fails_before_initialization(
    setup: AstcheckSetup, registry: RecordingRegistry, tmp_path: Path
) -> None:
    registry.error = AstcheckError("plugin is not installed", None)
    policy_file = tmp_path / "policy.yaml"
    policy_file.write_text(yaml.safe_dump(policy_document()), encoding="utf-8")

    with pytest.raises(ChecksmithError, match="plugin is not installed"):
        setup.policy(config_file=policy_file)


def test_invalid_plugin_settings_fail_before_initialization(
    setup: AstcheckSetup, tmp_path: Path
) -> None:
    policy_file = tmp_path / "policy.yaml"
    policy_file.write_text(
        "sources: [src]\nexclusions: []\nplugins: [{id: oopcheck, settings: {}}]",
        encoding="utf-8",
    )

    with pytest.raises(ChecksmithError, match="Invalid ASTcheck plugin configuration"):
        setup.policy(config_file=policy_file)


def test_setup_preserves_explicit_empty_lists(setup: AstcheckSetup) -> None:
    with CliRunner().isolation(input="application\n\n\n\n"):
        policy = setup.policy(config_file=None)

    assert policy.sources == ("application",)
    assert policy.exclusions == ()
    assert policy.plugins[0].settings == {
        "max_standalone_percent": 25,
        "min_callable_statements": 20,
        "min_shared_functions": 3,
        "collaborators": [],
    }


def test_setup_collects_collaborator_spelling_groups(setup: AstcheckSetup) -> None:
    answers = "src\n\n**/tests/**\n\nrepository\nRepository\nRepo\n\n\n"
    with CliRunner().isolation(input=answers):
        policy = setup.policy(config_file=None)

    assert policy.model_dump(mode="json") == policy_document()


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
    setup: AstcheckSetup, answers: str
) -> None:
    with (
        CliRunner().isolation(input=answers),
        pytest.raises(ChecksmithError, match="Invalid ASTcheck setup"),
    ):
        setup.policy(config_file=None)


@pytest.mark.parametrize("interactive", [False, True])
def test_no_missing_options_never_requires_a_terminal(
    registry: RecordingRegistry, interactive: bool
) -> None:
    setup = AstcheckSetup(
        registry=registry, terminal=ConfiguredTerminal(interactive=interactive)
    )

    setup.require_interactive(missing_options=())


def test_missing_options_require_a_terminal(registry: RecordingRegistry) -> None:
    setup = AstcheckSetup(
        registry=registry, terminal=ConfiguredTerminal(interactive=False)
    )

    with pytest.raises(ChecksmithError, match="--project-root and --astcheck-policy"):
        setup.require_interactive(
            missing_options=("--project-root", "--astcheck-policy")
        )


def test_interactive_input_allows_missing_options(setup: AstcheckSetup) -> None:
    setup.require_interactive(missing_options=("--astcheck-policy",))


def test_terminal_input_reads_the_current_stdin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    assert TerminalInput().is_interactive()
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert not TerminalInput().is_interactive()
