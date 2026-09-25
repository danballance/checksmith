import os
import stat
import sys
from pathlib import Path
from typing import Protocol, cast

import pytest

EXIT_CODE_OFFSET = 10


class _CoverageOptions(Protocol):
    cov_fail_under: float | None


class _CoveragePlugin(Protocol):
    options: _CoverageOptions


class _ChecksmithPlugin:
    def __init__(self) -> None:
        self.collection_failed: bool = False

    def pytest_collectreport(self, report: pytest.CollectReport) -> None:
        if report.failed:
            self.collection_failed = True

    @pytest.hookimpl(tryfirst=True)
    def pytest_collection(self, session: pytest.Session) -> bool | None:
        config = session.config
        if config.getoption("pyargs") or len(config.args) != 1:
            return None

        tests_path = Path.cwd() / "tests"
        selected_path = Path(os.path.abspath(config.args[0]))
        if selected_path != tests_path:
            return None

        try:
            tests_path.lstat()
        except FileNotFoundError:
            return True
        except OSError as error:
            raise pytest.UsageError(
                f"Cannot access default tests directory {tests_path}: {error}"
            ) from error

        try:
            mode = tests_path.stat().st_mode
        except OSError as error:
            raise pytest.UsageError(
                f"Cannot access default tests directory {tests_path}: {error}"
            ) from error

        if not stat.S_ISDIR(mode):
            raise pytest.UsageError(
                f"Default tests target must be a directory: {tests_path}"
            )
        return None

    @pytest.hookimpl(tryfirst=True)
    def pytest_runtestloop(self, session: pytest.Session) -> None:
        if self.collection_failed or session.testsfailed or session.items:
            return
        coverage_plugin = session.config.pluginmanager.get_plugin("_cov")
        if coverage_plugin is not None:
            cast(_CoveragePlugin, coverage_plugin).options.cov_fail_under = 0.0


def _coverage_config_arguments(*, arguments: list[str]) -> list[str]:
    normalized: list[str] = []
    index = 0
    while index < len(arguments):
        option = arguments[index]
        if option == "--":
            normalized.extend(arguments[index:])
            break
        if (
            option == "--cov-config"
            and index + 1 < len(arguments)
            and not arguments[index + 1].startswith("-")
        ):
            normalized.append(f"{option}={arguments[index + 1]}")
            index += 2
        else:
            normalized.append(option)
            index += 1
    return normalized


def main(*, args: list[str]) -> int:
    plugin = _ChecksmithPlugin()
    exit_code = int(
        pytest.main(_coverage_config_arguments(arguments=args), plugins=[plugin])
    )
    if exit_code not in range(7):
        print(f"pytest returned an unexpected exit code: {exit_code}", file=sys.stderr)
        return 2
    if plugin.collection_failed:
        return EXIT_CODE_OFFSET + pytest.ExitCode.INTERRUPTED
    return EXIT_CODE_OFFSET + exit_code


if __name__ == "__main__":
    sys.exit(main(args=sys.argv[1:]))
