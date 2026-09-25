import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from checksmith.commands.pytest import PytestCommand
from checksmith.config import Check
from checksmith.dtos import CheckStatus, CommandName, PackageType
from checksmith.runner import Runner


@pytest.fixture
def locked_uv_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    uv = shutil.which("uv")
    assert uv is not None, "uv must be available to run the project's test suite"

    for name in tuple(os.environ):
        if name.startswith(("UV_", "PYTHON", "PYTEST_")) or name in {
            "VIRTUAL_ENV",
            "CONDA_PREFIX",
            "__PYVENV_LAUNCHER__",
        }:
            monkeypatch.delenv(name)
    monkeypatch.setenv("PATH", os.pathsep.join((str(Path(uv).parent), os.defpath)))
    monkeypatch.setenv("UV_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("UV_OFFLINE", "1")
    monkeypatch.setenv("UV_NO_CONFIG", "1")
    monkeypatch.setenv("UV_PYTHON_DOWNLOADS", "never")
    monkeypatch.setenv("UV_PYTHON", str(Path(sys.executable).resolve()))

    project = tmp_path / "application"
    project.mkdir()
    (project / "pyproject.toml").write_text(
        '[project]\nname = "pytest-uv-isolation"\nversion = "0.1.0"\n'
        'requires-python = ">=3.14"\ndependencies = []\n',
        encoding="utf-8",
    )
    subprocess.run(
        (uv, "lock", "--offline", "--python", str(Path(sys.executable).resolve())),
        cwd=project,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=20,
    )
    return project


@pytest.mark.parametrize(
    ("problem", "message"),
    [
        ("missing_pytest", "No module named 'pytest'"),
        ("stale_lock", "needs to be updated"),
        ("malformed_config", "invalid pyproject.toml"),
        ("unmanaged_project", "managed"),
        ("sync_disabled", "UV_NO_SYNC"),
    ],
)
def test_real_uv_setup_errors_cannot_pass_an_absent_test_suite(
    locked_uv_project: Path,
    monkeypatch: pytest.MonkeyPatch,
    problem: str,
    message: str,
) -> None:
    project = locked_uv_project
    project_file = project / "pyproject.toml"
    lockfile = project / "uv.lock"
    locked_bytes = lockfile.read_bytes()
    if problem in {"stale_lock", "sync_disabled"}:
        project_file.write_text(
            project_file.read_text(encoding="utf-8").replace('"0.1.0"', '"0.2.0"'),
            encoding="utf-8",
        )
        if problem == "sync_disabled":
            monkeypatch.setenv("UV_NO_SYNC", "1")
    elif problem == "malformed_config":
        project_file.write_text("[project\n", encoding="utf-8")
    elif problem == "unmanaged_project":
        project_file.write_text(
            project_file.read_text(encoding="utf-8")
            + "\n[tool.uv]\nmanaged = false\n",
            encoding="utf-8",
        )

    check = Check(
        id="tests",
        package_type=PackageType.UV,
        package=None,
        command=CommandName.PYTEST,
        args=("tests",),
    )
    output = Runner(
        checks=(check,),
        commands={CommandName.PYTEST: PytestCommand()},
        project_root=project,
    ).check()

    assert len(output.results) == 1
    assert output.results[0].status is CheckStatus.ERROR
    assert message in "\n".join(output.results[0].messages)
    assert lockfile.read_bytes() == locked_bytes
    assert not (project / "tests").exists()
    if problem == "missing_pytest":
        assert (project / ".venv").is_dir()
