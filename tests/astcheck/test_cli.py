from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from astcheck.cli import app
from astcheck.domain.models import AnalysisReport


@pytest.fixture
def policy_file(tmp_path: Path) -> Path:
    config = tmp_path / "astcheck.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "project_root": ".",
                "sources": ["source.py"],
                "exclusions": [],
                "plugins": [
                    {
                        "id": "oopcheck",
                        "settings": {
                            "max_standalone_percent": 25,
                            "min_callable_statements": 20,
                            "min_shared_functions": 3,
                            "collaborators": [
                                {"name": "repository", "annotations": ["Repository"]}
                            ],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return config


@pytest.mark.parametrize(
    ("source", "exit_code", "rule_ids"),
    [
        ("class Service:\n    def run(self):\n        return 1\n", 0, set()),
        (
            "def run():\n" + "    x = 1\n" * 20,
            1,
            {"standalone-statement-share"},
        ),
        (
            (
                "def a(repo: Repository): return 1\n"
                "def b(repo: Repository): return 2\n"
                "def c(repo: Repository): return 3\n"
            ),
            1,
            {"repeated-collaborator"},
        ),
        ("def invalid(\n", 2, set()),
    ],
)
def test_cli_runs_installed_plugin_and_emits_clean_json(
    policy_file: Path, source: str, exit_code: int, rule_ids: set[str]
) -> None:
    (policy_file.parent / "source.py").write_text(source, encoding="utf-8")

    result = CliRunner().invoke(
        app, ["check", "--config", str(policy_file), "--format", "json"]
    )

    assert result.exit_code == exit_code, result.output
    report = AnalysisReport.model_validate_json(result.stdout)
    assert report.exit_code == exit_code
    assert {
        finding.rule_id for plugin in report.plugins for finding in plugin.findings
    } == rule_ids
    assert result.stderr == ""


def test_cli_text_output_names_the_plugin_and_evidence(policy_file: Path) -> None:
    (policy_file.parent / "source.py").write_text(
        "def run():\n" + "    x = 1\n" * 20, encoding="utf-8"
    )
    result = CliRunner().invoke(
        app, ["check", "--config", str(policy_file), "--format", "text"]
    )
    assert result.exit_code == 1
    assert "oopcheck/standalone-statement-share" in result.stdout
    assert "source.py:" in result.stdout
    assert "20" in result.stdout


def test_cli_configuration_error_is_a_json_report(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app, ["check", "--config", str(tmp_path / "absent.yaml"), "--format", "json"]
    )
    assert result.exit_code == 2
    assert "absent.yaml" in AnalysisReport.model_validate_json(
        result.stdout
    ).errors[0].message


@pytest.mark.parametrize(
    "arguments",
    [
        ["check"],
        ["check", "--config", "astcheck.yaml"],
        ["check", "--format", "json"],
        ["check", "--config", "astcheck.yaml", "--format", "xml"],
    ],
)
def test_cli_requires_explicit_valid_options(arguments: list[str]) -> None:
    result = CliRunner().invoke(app, arguments)
    assert result.exit_code == 2


def test_cli_help_exposes_check_command() -> None:
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "check" in result.stdout
