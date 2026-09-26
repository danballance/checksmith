import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Literal

import pytest
from pydantic import BaseModel

from checksmith.packages import UvxPackage

PROBE_URL = "https://checksmith.invalid/probe.git"
PROBE_PACKAGE = f"checksmith-cache-probe @ git+{PROBE_URL}@main"
PROBE_COMMAND = "checksmith-cache-probe"
BUILD_BACKEND = '''\
import os
from pathlib import Path
from zipfile import ZipFile


def build_wheel(
    wheel_directory: str,
    config_settings: object = None,
    metadata_directory: str | None = None,
) -> str:
    filename = "checksmith_cache_probe-0.1.0-py3-none-any.whl"
    info = "checksmith_cache_probe-0.1.0.dist-info"
    contents = {
        "cache_probe.py": Path("cache_probe.py").read_text(encoding="utf-8"),
        f"{info}/METADATA": (
            "Metadata-Version: 2.3\\nName: checksmith-cache-probe\\n"
            "Version: 0.1.0\\nRequires-Python: >=3.14\\n"
        ),
        f"{info}/WHEEL": (
            "Wheel-Version: 1.0\\nGenerator: checksmith-cache-test\\n"
            "Root-Is-Purelib: true\\nTag: py3-none-any\\n"
        ),
        f"{info}/entry_points.txt": (
            "[console_scripts]\\nchecksmith-cache-probe = cache_probe:main\\n"
        ),
    }
    contents[f"{info}/RECORD"] = "".join(
        f"{name},,\\n" for name in (*contents, f"{info}/RECORD")
    )
    with ZipFile(Path(wheel_directory) / filename, "w") as wheel:
        for name, content in contents.items():
            wheel.writestr(name, content)
    with Path(os.environ["CHECKSMITH_PROBE_BUILD_LOG"]).open(
        "a", encoding="utf-8"
    ) as log:
        log.write("built\\n")
    return filename
'''


class ProbeProject(BaseModel):
    repository: Path
    working_directory: Path
    build_log: Path
    installed_command: Path
    uv: str
    git: str


class ProbeOutput(BaseModel):
    revision: Literal["A", "B"]
    environment: Path


def _run(
    *, argv: tuple[str, ...], cwd: Path, check: bool
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=check,
        timeout=30,
    )


def _commit_revision(*, project: ProbeProject, revision: Literal["A", "B"]) -> None:
    (project.repository / "cache_probe.py").write_text(
        "import json\nimport sys\n\n\n"
        "def main() -> None:\n"
        f"    print(json.dumps({{'revision': {revision!r}, "
        "'environment': sys.prefix}))\n",
        encoding="utf-8",
    )
    _run(argv=(project.git, "add", "."), cwd=project.repository, check=True)
    _run(
        argv=(
            project.git,
            "-c",
            "user.name=Checksmith test",
            "-c",
            "user.email=checksmith@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-m",
            revision,
        ),
        cwd=project.repository,
        check=True,
    )


@pytest.fixture
def git_probe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ProbeProject:
    uv = shutil.which("uv")
    uvx = shutil.which("uvx")
    git = shutil.which("git")
    assert uv is not None, "uv must be available to test Git package caching"
    assert uvx is not None, "uvx must be available to test Git package caching"
    assert git is not None, "git must be available to test Git package caching"

    for name in tuple(os.environ):
        if name.startswith(("UV_", "PYTHON", "PYTEST_", "GIT_")) or name in {
            "VIRTUAL_ENV",
            "CONDA_PREFIX",
            "__PYVENV_LAUNCHER__",
        }:
            monkeypatch.delenv(name)
    monkeypatch.setenv(
        "PATH",
        os.pathsep.join(
            (str(Path(uv).parent), str(Path(uvx).parent), str(Path(git).parent), os.defpath)
        ),
    )
    monkeypatch.setenv("UV_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("UV_TOOL_DIR", str(tmp_path / "tools"))
    monkeypatch.setenv("UV_TOOL_BIN_DIR", str(tmp_path / "bin"))
    monkeypatch.setenv("UV_NO_CONFIG", "1")
    monkeypatch.setenv("UV_NO_INDEX", "1")
    monkeypatch.setenv("UV_PYTHON_DOWNLOADS", "never")
    monkeypatch.setenv("UV_PYTHON", str(Path(sys.executable).resolve()))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    monkeypatch.setenv("GIT_ALLOW_PROTOCOL", "file")

    repository = tmp_path / "repository"
    repository.mkdir()
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv(
        "GIT_CONFIG_KEY_0", f"url.{repository.as_uri()}.insteadOf"
    )
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", PROBE_URL)
    build_log = tmp_path / "builds.log"
    monkeypatch.setenv("CHECKSMITH_PROBE_BUILD_LOG", str(build_log))

    _run(
        argv=(git, "init", "--initial-branch=main"), cwd=repository, check=True
    )
    (repository / "pyproject.toml").write_text(
        '[project]\nname = "checksmith-cache-probe"\nversion = "0.1.0"\n'
        'requires-python = ">=3.14"\ndependencies = []\n'
        '[build-system]\nrequires = []\nbuild-backend = "backend"\n'
        'backend-path = ["."]\n',
        encoding="utf-8",
    )
    (repository / "backend.py").write_text(BUILD_BACKEND, encoding="utf-8")
    project = ProbeProject(
        repository=repository,
        working_directory=tmp_path,
        build_log=build_log,
        installed_command=tmp_path / "bin" / PROBE_COMMAND,
        uv=uv,
        git=git,
    )
    _commit_revision(project=project, revision="A")
    return project


def test_git_branch_refresh_reuses_commits_and_never_runs_a_stale_tool(
    git_probe: ProbeProject,
) -> None:
    project = git_probe
    _run(
        argv=(project.uv, "tool", "install", PROBE_PACKAGE),
        cwd=project.working_directory,
        check=True,
    )
    installed = ProbeOutput.model_validate_json(
        _run(
            argv=(str(project.installed_command),),
            cwd=project.working_directory,
            check=True,
        ).stdout
    )
    assert installed.revision == "A"

    package = UvxPackage()
    package.validate_package(package=PROBE_PACKAGE)
    argv = package.build_argv(
        package=PROBE_PACKAGE, command=PROBE_COMMAND, arguments=()
    )
    first = ProbeOutput.model_validate_json(
        _run(argv=argv, cwd=project.working_directory, check=True).stdout
    )
    assert first.revision == "A"
    assert first.environment != installed.environment
    first_builds = project.build_log.read_text(encoding="utf-8")
    assert first_builds

    repeated = ProbeOutput.model_validate_json(
        _run(argv=argv, cwd=project.working_directory, check=True).stdout
    )
    assert repeated == first
    assert project.build_log.read_text(encoding="utf-8") == first_builds

    _commit_revision(project=project, revision="B")
    updated = ProbeOutput.model_validate_json(
        _run(argv=argv, cwd=project.working_directory, check=True).stdout
    )
    assert updated.revision == "B"
    assert updated.environment != first.environment
    updated_builds = project.build_log.read_text(encoding="utf-8")
    assert len(updated_builds) > len(first_builds)
    assert ProbeOutput.model_validate_json(
        _run(
            argv=(str(project.installed_command),),
            cwd=project.working_directory,
            check=True,
        ).stdout
    ) == installed

    project.repository.rename(project.repository.with_name("unavailable-repository"))
    unavailable = _run(argv=argv, cwd=project.working_directory, check=False)
    assert unavailable.returncode != 0
    assert unavailable.stdout == ""
    assert "failed to fetch" in unavailable.stderr.lower()
    assert project.build_log.read_text(encoding="utf-8") == updated_builds
