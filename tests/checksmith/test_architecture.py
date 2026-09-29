import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT = Path(__file__).parents[2]


def run_contracts(directory: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-c",
            "from importlinter.cli import lint_imports_command; lint_imports_command()",
            "--config",
            str(PROJECT / "pyproject.toml"),
            "--no-cache",
            "--no-logo",
        ],
        cwd=directory,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )


def test_repository_satisfies_import_contracts() -> None:
    result = run_contracts(PROJECT)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "7 kept, 0 broken" in result.stdout


@pytest.mark.parametrize("package", ["checksmith", "astcheck"])
@pytest.mark.parametrize(
    ("source", "dependency", "contract"),
    [
        ("domain/__init__.py", "application", "Dependencies point inward"),
        ("domain/__init__.py", "rich", "Core layers do not depend"),
        ("application/ports/__init__.py", "subprocess", "Core layers do not depend"),
        ("domain/__init__.py", "other_package", "communicate only through"),
        ("adapters/driving/cli/__init__.py", "adapters.driven.configuration", "adapter implementations are independent"),
        ("domain/__init__.py", "main", "Only composition roots"),
        ("application/ports/__init__.py", "application.use_cases", "Application ports are independent"),
    ],
)
def test_contracts_reject_outward_and_cross_adapter_dependencies(
    tmp_path: Path, package: str, source: str, dependency: str, contract: str
) -> None:
    for name in ("checksmith", "astcheck"):
        shutil.copytree(
            PROJECT / name,
            tmp_path / name,
            ignore=shutil.ignore_patterns("__pycache__"),
        )
    if dependency == "other_package":
        target = "astcheck" if package == "checksmith" else "checksmith"
    elif dependency in ("rich", "subprocess"):
        target = dependency
    else:
        target = f"{package}.{dependency}"
    path = tmp_path / package / source
    path.write_text(path.read_text() + f"\nimport {target}\n")

    result = run_contracts(tmp_path)

    assert result.returncode == 1, result.stdout + result.stderr
    assert any(
        contract in line and "BROKEN" in line for line in result.stdout.splitlines()
    ), result.stdout
