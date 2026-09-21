"""Shared fixtures for the Checksmith test suite."""

import logging
import subprocess
import sys
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict

from checksmith.logs import LOGGER_NAME


@pytest.fixture
def imported_modules() -> Callable[[str], frozenset[str]]:
    def import_in_fresh_process(module: str) -> frozenset[str]:
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import importlib, sys\n"
                    "importlib.import_module(sys.argv[1])\n"
                    "sys.stdout.write('\\n'.join(sys.modules))\n"
                ),
                module,
            ],
            cwd=Path(__file__).resolve().parent.parent,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
            timeout=10,
        )
        return frozenset(completed.stdout.splitlines())

    return import_in_fresh_process


@pytest.fixture(autouse=True)
def restore_the_package_logger() -> Iterator[None]:
    """Put the ``checksmith`` logger back as every test found it.

    ``configure_logging`` mutates process-global state, and the CLI calls it on
    every invocation. Without this, one test would decide what the next logs.
    """
    logger = logging.getLogger(LOGGER_NAME)
    handlers = list(logger.handlers)
    level = logger.level
    propagate = logger.propagate
    yield
    logger.handlers = handlers
    logger.setLevel(level)
    logger.propagate = propagate


CONFIG_TEXT = """\
schema_version: 1

project_root: ..

checks:
  - id: ruff
    package_type: uvx
    package: "ruff==0.16.7"
    command: ruff

    args:
      - check
      - --config
      - config_path: ruff.toml
      - --output-format
      - json
      - .
"""

RUFF_TOML_TEXT = """\
target-version = "py312"

[lint]
select = ["E", "F"]
"""


@pytest.fixture
def config_tree(tmp_path: Path) -> Path:
    """A project directory holding ``.checksmith/{checksmith.yaml,ruff.toml}``.

    Returns the directory the ``.checksmith`` directory sits in.
    """
    directory = tmp_path / ".checksmith"
    directory.mkdir()
    (directory / "checksmith.yaml").write_text(CONFIG_TEXT, encoding="utf-8")
    (directory / "ruff.toml").write_text(RUFF_TOML_TEXT, encoding="utf-8")
    return tmp_path


@pytest.fixture
def config_file(config_tree: Path) -> Path:
    """The config file inside :func:`config_tree`, which every run must name."""
    return config_tree / ".checksmith" / "checksmith.yaml"


class StartedProcess(BaseModel):
    """One process a command started, as it asked for it."""

    model_config = ConfigDict(frozen=True)

    argv: tuple[str, ...]
    cwd: Path
    """Recorded as a path, though the standard library is handed a string."""

    stdin: int


class FakeProcesses:
    """Every process the code under test started, and the answer they all get."""

    def __init__(self) -> None:
        self.started: list[StartedProcess] = []
        """One entry per call, in the order the calls were made."""

        self.exit_code = 0
        self.stdout = ""
        self.stderr = ""
        """What every process reports. A test that wants another sets them."""

        self.refusal: OSError | None = None
        """Set to refuse to start at all, as an absent program would."""

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: str,
        stdin: int,
        capture_output: bool,
        text: bool,
        encoding: str,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        """Stand in for :func:`subprocess.run`, recording rather than starting.

        The signature is itself the assertion: a caller that stopped passing any
        one of these explicitly fails here with a ``TypeError`` rather than
        quietly taking the standard library's default for it.
        """
        if self.refusal is not None:
            # Nothing is recorded: a process that never started is not one.
            raise self.refusal
        self.started.append(StartedProcess(argv=tuple(argv), cwd=cwd, stdin=stdin))
        return subprocess.CompletedProcess(
            args=list(argv),
            returncode=self.exit_code,
            stdout=self.stdout,
            stderr=self.stderr,
        )


@pytest.fixture
def processes(monkeypatch: pytest.MonkeyPatch) -> FakeProcesses:
    """No test starts a real process; every test can see how one was asked for.

    Without this, any test reaching :meth:`Command.run` would fetch a tool from
    the network and run it --- slow, and answering differently on every machine.
    """
    fake = FakeProcesses()
    monkeypatch.setattr(subprocess, "run", fake.run)
    return fake
