import json
from pathlib import Path

import pytest
from pydantic import BaseModel

from checksmith.adapters.driven.astcheck_setup.process import SetupProcessResult
from checksmith.adapters.driven.astcheck_setup.setup import AstcheckSetup
from checksmith.adapters.driven.execution.packages import UvxInvocation
from checksmith.domain.errors import ChecksmithError


class ProcessCall(BaseModel):
    argv: tuple[str, ...]
    cwd: Path
    interactive: bool


class RecordingProcess:
    def __init__(self) -> None:
        self.calls: list[ProcessCall] = []
        self.result = SetupProcessResult(
            returncode=0,
            stdout=json.dumps(
                {"schema_version": 1, "configuration_yaml": "schema_version: 1\n"}
            ).encode(),
        )
        self.error: OSError | KeyboardInterrupt | None = None

    def run(
        self, *, argv: tuple[str, ...], cwd: Path, interactive: bool
    ) -> SetupProcessResult:
        self.calls.append(ProcessCall(argv=argv, cwd=cwd, interactive=interactive))
        if self.error is not None:
            raise self.error
        return self.result


@pytest.fixture
def process() -> RecordingProcess:
    return RecordingProcess()


@pytest.fixture
def setup(process: RecordingProcess) -> AstcheckSetup:
    return AstcheckSetup(process=process, commands=UvxInvocation())


@pytest.mark.parametrize("interactive", [False, True])
def test_setup_builds_external_command_from_supplied_package(
    setup: AstcheckSetup,
    process: RecordingProcess,
    tmp_path: Path,
    interactive: bool,
) -> None:
    policy = None if interactive else tmp_path / "policy with spaces.yaml"
    package = "checksmith @ git+https://github.com/danballance/checksmith@main"

    content = setup.configure(
        package=package,
        project_root=tmp_path,
        config_file=tmp_path / "configuration with spaces.yaml",
        policy=policy,
    )

    assert content == b"schema_version: 1\n"
    assert process.calls == [
        ProcessCall(
            argv=(
                "uvx",
                "--isolated",
                "--refresh-package",
                "checksmith",
                "--from",
                package,
                "astcheck",
                "configure",
                "--project-root",
                str(tmp_path),
                "--config-file",
                str(tmp_path / "configuration with spaces.yaml"),
                *(("--interactive",) if interactive else ("--policy", str(policy))),
                "--format",
                "json",
            ),
            cwd=tmp_path,
            interactive=interactive,
        )
    ]


@pytest.mark.parametrize("path_argument", ["project_root", "config_file", "policy"])
def test_setup_rejects_relative_paths_before_running(
    setup: AstcheckSetup, process: RecordingProcess, tmp_path: Path, path_argument: str
) -> None:
    with pytest.raises(ChecksmithError, match="requires absolute paths"):
        setup.configure(
            package="checksmith==1.0",
            project_root=Path("project")
            if path_argument == "project_root"
            else tmp_path,
            config_file=Path("config")
            if path_argument == "config_file"
            else tmp_path / "config",
            policy=Path("policy") if path_argument == "policy" else tmp_path / "policy",
        )
    assert not process.calls


@pytest.mark.parametrize("returncode", [1, 2, 127, -2])
def test_configuration_failures_propagate_exit_code(
    setup: AstcheckSetup, process: RecordingProcess, tmp_path: Path, returncode: int
) -> None:
    process.result = SetupProcessResult(returncode=returncode, stdout=b"")
    with pytest.raises(ChecksmithError, match=f"exit code {returncode}"):
        setup.configure(
            package="checksmith==1.0",
            project_root=tmp_path,
            config_file=tmp_path / "config",
            policy=None,
        )


@pytest.mark.parametrize(
    "response",
    [
        b"",
        b"not JSON",
        b"[]",
        b"null",
        b"{}",
        b"\xff",
        b'{"schema_version":1,"configuration_yaml":"ok","extra":true}',
        b'{"schema_version":2,"configuration_yaml":"ok"}',
        b'{"schema_version":true,"configuration_yaml":"ok"}',
        b'{"schema_version":1.0,"configuration_yaml":"ok"}',
        b'{"schema_version":"1","configuration_yaml":"ok"}',
        b'{"schema_version":1,"configuration_yaml":null}',
        b'{"schema_version":1,"configuration_yaml":123}',
        b'{"schema_version":1,"configuration_yaml":""}',
        b'{"schema_version":1,"configuration_yaml":"  "}',
        b'{"schema_version":1,"configuration_yaml":"ok"} {}',
        b'{"schema_version":1,"schema_version":1,"configuration_yaml":"ok"}',
        b'{"schema_version":1,"configuration_yaml":"\\ud800"}',
    ],
)
def test_invalid_external_responses_fail_clearly(
    setup: AstcheckSetup, process: RecordingProcess, tmp_path: Path, response: bytes
) -> None:
    process.result = SetupProcessResult(returncode=0, stdout=response)
    with pytest.raises(ChecksmithError, match="Invalid ASTcheck configure response"):
        setup.configure(
            package="checksmith==1.0",
            project_root=tmp_path,
            config_file=tmp_path / "config",
            policy=tmp_path / "policy",
        )


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (FileNotFoundError("uvx unavailable"), "Could not run"),
        (KeyboardInterrupt(), "cancelled"),
    ],
)
def test_process_failures_have_setup_context(
    setup: AstcheckSetup,
    process: RecordingProcess,
    tmp_path: Path,
    error: OSError | KeyboardInterrupt,
    message: str,
) -> None:
    process.error = error
    with pytest.raises(ChecksmithError, match=message):
        setup.configure(
            package="checksmith==1.0",
            project_root=tmp_path,
            config_file=tmp_path / "config",
            policy=None,
        )
