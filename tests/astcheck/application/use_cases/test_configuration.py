from pathlib import Path

import pytest

from astcheck.application.use_cases.configuration import ConfigureService
from astcheck.domain.configuration import AnalysisPolicy
from astcheck.domain.errors import AstcheckError


class RecordingLoader:
    def __init__(self, policy: AnalysisPolicy, events: list[str]) -> None:
        self.policy = policy
        self.events = events

    def load(self, *, path: Path) -> AnalysisPolicy:
        self.events.append(f"load:{path}")
        return self.policy


class RecordingPrompts:
    def __init__(self, policy: AnalysisPolicy, events: list[str]) -> None:
        self.policy = policy
        self.events = events

    def prompt(self) -> AnalysisPolicy:
        self.events.append("prompt")
        return self.policy


class RecordingValidator:
    def __init__(self, events: list[str], error: AstcheckError | None) -> None:
        self.events = events
        self.error = error

    def validate_policy(self, *, policy: AnalysisPolicy) -> None:
        self.events.append("validate")
        if self.error is not None:
            raise self.error


class RecordingRenderer:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.arguments: tuple[Path, Path, AnalysisPolicy] | None = None

    def render(
        self, *, project_root: Path, config_file: Path, policy: AnalysisPolicy
    ) -> str:
        self.events.append("render")
        self.arguments = (project_root, config_file, policy)
        return "rendered YAML"


@pytest.mark.parametrize("interactive", [False, True])
def test_setup_prepares_and_validates_policy_before_rendering(
    analysis_policy: AnalysisPolicy, tmp_path: Path, interactive: bool
) -> None:
    events: list[str] = []
    renderer = RecordingRenderer(events)
    service = ConfigureService(
        policies=RecordingLoader(analysis_policy, events),
        prompts=RecordingPrompts(analysis_policy, events),
        validator=RecordingValidator(events, None),
        renderer=renderer,
    )
    policy_file = None if interactive else tmp_path / "policy.yaml"
    config_file = tmp_path / "config/astcheck.yaml"

    assert (
        service.run(
            project_root=tmp_path, config_file=config_file, policy_file=policy_file
        )
        == "rendered YAML"
    )
    assert events == [
        "prompt" if interactive else f"load:{policy_file}",
        "validate",
        "render",
    ]
    assert renderer.arguments == (tmp_path, config_file, analysis_policy)


@pytest.mark.parametrize(
    "relative_option", ["project_root", "config_file", "policy_file"]
)
def test_relative_paths_fail_before_policy_loading_or_prompts(
    analysis_policy: AnalysisPolicy, tmp_path: Path, relative_option: str
) -> None:
    events: list[str] = []
    service = ConfigureService(
        policies=RecordingLoader(analysis_policy, events),
        prompts=RecordingPrompts(analysis_policy, events),
        validator=RecordingValidator(events, None),
        renderer=RecordingRenderer(events),
    )
    with pytest.raises(AstcheckError, match="must be an absolute path"):
        service.run(
            project_root=Path("project")
            if relative_option == "project_root"
            else tmp_path,
            config_file=Path("config.yaml")
            if relative_option == "config_file"
            else tmp_path / "config.yaml",
            policy_file=Path("policy.yaml")
            if relative_option == "policy_file"
            else tmp_path / "policy.yaml",
        )
    assert events == []


def test_plugin_failure_prevents_configuration_rendering(
    analysis_policy: AnalysisPolicy, tmp_path: Path
) -> None:
    events: list[str] = []
    service = ConfigureService(
        policies=RecordingLoader(analysis_policy, events),
        prompts=RecordingPrompts(analysis_policy, events),
        validator=RecordingValidator(events, AstcheckError("missing plugin", None)),
        renderer=RecordingRenderer(events),
    )
    with pytest.raises(AstcheckError, match="missing plugin"):
        service.run(
            project_root=tmp_path,
            config_file=tmp_path / "config.yaml",
            policy_file=tmp_path / "policy.yaml",
        )
    assert events == [f"load:{tmp_path / 'policy.yaml'}", "validate"]
