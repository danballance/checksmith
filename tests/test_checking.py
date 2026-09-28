from collections.abc import Mapping
from pathlib import Path

import pytest

from checksmith.checking import CheckService
from checksmith.commands.command import Command
from checksmith.config import Check, Config
from checksmith.dtos import CheckResult, CheckStatus, CommandName
from checksmith.errors import ChecksmithError, ConfigSyntaxError
from checksmith.processes import ProcessOutput


class RecordingLoader:
    def __init__(self, config: Config | ConfigSyntaxError, events: list[str]) -> None:
        self.config = config
        self.events = events
        self.arguments: list[tuple[Path, Path]] = []

    def load(self, *, config_file: Path, working_directory: Path) -> Config:
        self.events.append("load")
        self.arguments.append((config_file, working_directory))
        if isinstance(self.config, ConfigSyntaxError):
            raise self.config
        return self.config


class RecordingCommand(Command):
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.roots: list[Path] = []

    @property
    def name(self) -> CommandName:
        return CommandName.RUFF

    def run(
        self, *, check: Check, project_root: Path, output: ProcessOutput | None
    ) -> CheckResult:
        self.events.append(check.id)
        self.roots.append(project_root)
        return CheckResult(check_id=check.id, status=CheckStatus.PASSED, messages=())

    def process_response(
        self,
        *,
        check_id: str,
        project_root: Path,
        exit_code: int,
        stdout: str,
        stderr: str,
    ) -> CheckResult:
        raise AssertionError("Recording command does not parse process output")


class RecordingRegistry:
    def __init__(self, command: Command, events: list[str]) -> None:
        self.command = command
        self.events = events

    def registry(self) -> Mapping[CommandName, Command]:
        self.events.append("registry")
        return {CommandName.RUFF: self.command}


@pytest.fixture
def config() -> Config:
    return Config.from_mapping(
        config_file=Path("/project/.checksmith/checksmith.yaml"),
        document={
            "schema_version": 1,
            "project_root": "..",
            "checks": [
                {
                    "id": name,
                    "package_type": "uvx",
                    "package": "ruff==0.16.7",
                    "command": "ruff",
                    "args": ["check"],
                }
                for name in ("first", "second")
            ],
        },
    )


@pytest.mark.parametrize("selected", [None, "second"])
def test_service_loads_before_constructing_and_running_selected_checks(
    config: Config, selected: str | None
) -> None:
    events: list[str] = []
    loader = RecordingLoader(config=config, events=events)
    command = RecordingCommand(events=events)
    service = CheckService(
        loader=loader, commands=RecordingRegistry(command=command, events=events)
    )

    output = service.check(
        config_file=Path("settings.yaml"),
        working_directory=Path("/invocation"),
        check_id=selected,
        output=None,
    )

    selected_ids = ["first", "second"] if selected is None else ["second"]
    assert events == ["load", "registry", *selected_ids]
    assert loader.arguments == [(Path("settings.yaml"), Path("/invocation"))]
    assert [result.check_id for result in output.results] == selected_ids
    assert command.roots == [Path("/project")] * len(selected_ids)


@pytest.mark.parametrize("selected", ["missing", "", "FIRST"])
def test_unknown_selection_fails_before_constructing_commands(
    config: Config, selected: str
) -> None:
    events: list[str] = []
    service = CheckService(
        loader=RecordingLoader(config=config, events=events),
        commands=RecordingRegistry(
            command=RecordingCommand(events=events), events=events
        ),
    )

    with pytest.raises(ChecksmithError) as raised:
        service.check(
            config_file=Path("settings.yaml"),
            working_directory=Path("/invocation"),
            check_id=selected,
            output=None,
        )

    assert str(raised.value) == (
        f"Unknown check ID {selected!r}. Available check IDs: 'first', 'second'."
    )
    assert events == ["load"]


def test_configuration_failure_does_not_construct_commands() -> None:
    events: list[str] = []
    error = ConfigSyntaxError(config_file=Path("/settings.yaml"), problem="bad YAML")
    service = CheckService(
        loader=RecordingLoader(config=error, events=events),
        commands=RecordingRegistry(
            command=RecordingCommand(events=events), events=events
        ),
    )

    with pytest.raises(ConfigSyntaxError) as raised:
        service.check(
            config_file=Path("settings.yaml"),
            working_directory=Path("/invocation"),
            check_id=None,
            output=None,
        )

    assert raised.value is error
    assert events == ["load"]
