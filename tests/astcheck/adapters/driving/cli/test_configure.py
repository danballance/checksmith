import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from astcheck.adapters.driving.cli.setup import ConfigurationResponse, TerminalInput
from astcheck.domain.configuration import AnalysisConfiguration, AnalysisPolicy
from astcheck.main import app


@pytest.fixture
def setup_policy_file(analysis_policy: AnalysisPolicy, tmp_path: Path) -> Path:
    path = tmp_path / "policy.yaml"
    path.write_text(
        yaml.safe_dump(analysis_policy.model_dump(mode="json")), encoding="utf-8"
    )
    return path


def configure_arguments(project_root: Path, config_file: Path) -> list[str]:
    return [
        "configure",
        "--project-root",
        str(project_root),
        "--config-file",
        str(config_file),
        "--format",
        "json",
    ]


def test_policy_mode_emits_one_json_object_and_never_prompts_or_writes(
    setup_policy_file: Path, tmp_path: Path
) -> None:
    destination = tmp_path / "not-created/astcheck.yaml"
    result = CliRunner().invoke(
        app,
        [
            *configure_arguments(tmp_path, destination),
            "--policy",
            str(setup_policy_file),
        ],
    )

    assert result.exit_code == 0, result.output
    response = ConfigurationResponse.model_validate_json(result.stdout)
    assert len(result.stdout.splitlines()) == 1
    assert set(json.loads(result.stdout)) == {"schema_version", "configuration_yaml"}
    configuration = AnalysisConfiguration.model_validate(
        yaml.safe_load(response.configuration_yaml)
    )
    assert configuration.project_root == Path("..")
    assert result.stderr == ""
    assert not destination.parent.exists()


@pytest.mark.parametrize("both", [False, True])
def test_configure_requires_exactly_one_setup_mode(
    setup_policy_file: Path, tmp_path: Path, both: bool
) -> None:
    arguments = configure_arguments(tmp_path, tmp_path / "astcheck.yaml")
    if both:
        arguments.extend(["--policy", str(setup_policy_file), "--interactive"])
    result = CliRunner().invoke(app, arguments)

    assert result.exit_code == 2
    assert "exactly one" in result.stderr
    assert result.stdout == ""


def test_configure_requires_terminal_for_interactive_mode(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app,
        [*configure_arguments(tmp_path, tmp_path / "astcheck.yaml"), "--interactive"],
        input="src\n\n\n\n",
    )
    assert result.exit_code == 2
    assert "terminal input" in result.stderr
    assert result.stdout == ""


def test_interactive_cancellation_exits_two_without_stdout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(TerminalInput, "is_interactive", lambda self: True)
    result = CliRunner().invoke(
        app,
        [*configure_arguments(tmp_path, tmp_path / "astcheck.yaml"), "--interactive"],
        input="",
    )

    assert result.exit_code == 2
    assert "configuration cancelled" in result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize("option", ["--project-root", "--config-file", "--policy"])
def test_configure_rejects_relative_paths(
    setup_policy_file: Path, tmp_path: Path, option: str
) -> None:
    arguments = [
        *configure_arguments(tmp_path, tmp_path / "astcheck.yaml"),
        "--policy",
        str(setup_policy_file),
    ]
    arguments[arguments.index(option) + 1] = "."
    result = CliRunner().invoke(app, arguments)

    assert result.exit_code == 2
    assert "absolute path" in result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("sources: [", "Could not read ASTcheck policy"),
        ("sources: [src]", "Invalid ASTcheck policy"),
        (
            "sources: [src]\nexclusions: []\nplugins: [{id: missing, settings: {}}]",
            "plugin 'missing' is not installed",
        ),
        (
            "sources: [src]\nexclusions: []\nplugins: [{id: oopcheck, settings: {}}]",
            "Could not configure plugin 'oopcheck'",
        ),
    ],
)
def test_configuration_failures_are_stderr_only_and_exit_two(
    tmp_path: Path, content: str, message: str
) -> None:
    policy = tmp_path / "policy.yaml"
    policy.write_text(content, encoding="utf-8")
    destination = tmp_path / "astcheck.yaml"
    result = CliRunner().invoke(
        app, [*configure_arguments(tmp_path, destination), "--policy", str(policy)]
    )

    assert result.exit_code == 2
    assert message in result.stderr
    assert result.stdout == ""
    assert not destination.exists()


@pytest.mark.parametrize("omitted", ["--project-root", "--config-file", "--format"])
def test_configure_requires_explicit_options(tmp_path: Path, omitted: str) -> None:
    arguments = configure_arguments(tmp_path, tmp_path / "astcheck.yaml")
    index = arguments.index(omitted)
    del arguments[index : index + 2]
    result = CliRunner().invoke(app, [*arguments, "--interactive"])
    assert result.exit_code == 2


def test_configure_only_supports_json(tmp_path: Path) -> None:
    arguments = configure_arguments(tmp_path, tmp_path / "astcheck.yaml")
    arguments[-1] = "text"
    result = CliRunner().invoke(app, [*arguments, "--interactive"])
    assert result.exit_code == 2


def test_policy_mode_runs_in_a_real_noninteractive_process(
    setup_policy_file: Path, tmp_path: Path
) -> None:
    destination = tmp_path / "astcheck.yaml"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "astcheck.main",
            *configure_arguments(tmp_path, destination),
            "--policy",
            str(setup_policy_file),
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
        cwd=tmp_path,
    )

    assert result.returncode == 0, result.stderr
    assert ConfigurationResponse.model_validate_json(result.stdout).schema_version == 1
    assert result.stderr == ""
    assert not destination.exists()


@pytest.mark.parametrize("cancelled", [False, True])
def test_interactive_mode_uses_real_terminal_and_keeps_stdout_clean(
    tmp_path: Path, cancelled: bool
) -> None:
    destination = tmp_path / "astcheck.yaml"
    master, slave = os.openpty()
    try:
        with subprocess.Popen(
            [
                sys.executable,
                "-m",
                "astcheck.main",
                *configure_arguments(tmp_path, destination),
                "--interactive",
            ],
            stdin=slave,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=tmp_path,
        ) as process:
            os.write(master, b"\x04" if cancelled else b"src\n\n\n\n")
            stdout, stderr = process.communicate(timeout=15)
        assert process.returncode == (2 if cancelled else 0), stderr
        assert "Source path relative to project root" in stderr
        if cancelled:
            assert stdout == ""
            assert "configuration cancelled" in stderr
        else:
            response = ConfigurationResponse.model_validate_json(stdout)
            configuration = AnalysisConfiguration.model_validate(
                yaml.safe_load(response.configuration_yaml)
            )
            assert configuration.sources == ("src",)
            assert "Exclusion glob" in stderr
            assert "Collaborator name" in stderr
        assert not destination.exists()
    finally:
        os.close(master)
        os.close(slave)
