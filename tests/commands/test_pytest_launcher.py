import importlib.util
import os
import subprocess
import sys
from collections.abc import Callable, Iterator
from importlib.machinery import ModuleSpec
from importlib.resources import as_file, files
from io import StringIO
from pathlib import Path
from typing import Protocol

import pytest
from coverage import Coverage

from checksmith.commands import _pytest_launcher
from checksmith.commands.pytest import PytestSummary
from checksmith.commands.registry import CommandFactory
from checksmith.configuration_loading import ConfigLoader, LocalYamlConfigSource
from checksmith.dtos import CheckStatus, CommandName
from checksmith.errors import CheckOutputError


@pytest.fixture
def run_launcher(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Callable[[list[str]], int]:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
    monkeypatch.delenv("PYTEST_ADDOPTS", raising=False)
    (tmp_path / "pytest.ini").write_text("[pytest]\n")

    def run(args: list[str]) -> int:
        module_names = set(sys.modules)
        try:
            return _pytest_launcher.PytestLauncher(
                invoke_pytest=pytest.main, error_stream=sys.stderr
            ).run(
                args=["-q", "--import-mode=importlib", "-p", "no:cacheprovider", *args],
                report_path=tmp_path / "summary.json",
            )
        finally:
            for name in set(sys.modules) - module_names:
                module_path = getattr(sys.modules[name], "__file__", None)
                if module_path and Path(module_path).is_relative_to(tmp_path):
                    del sys.modules[name]

    return run


@pytest.mark.parametrize("exit_code", range(7))
def test_known_pytest_exit_codes_are_distinct_from_uv_failures(
    exit_code: int,
    tmp_path: Path,
) -> None:
    def fake_main(args: list[str], plugins: list[object]) -> int:
        assert args == ["tests", "-x"]
        assert len(plugins) == 1
        assert isinstance(plugins[0], _pytest_launcher._ChecksmithPlugin)
        return exit_code

    launcher = _pytest_launcher.PytestLauncher(
        invoke_pytest=fake_main, error_stream=StringIO()
    )

    assert (
        launcher.run(args=["tests", "-x"], report_path=tmp_path / "summary.json")
        == 10 + exit_code
    )


def test_unknown_pytest_exit_code_is_an_execution_error(tmp_path: Path) -> None:
    def fake_main(args: list[str], plugins: list[object]) -> int:
        return 42

    error_stream = StringIO()
    launcher = _pytest_launcher.PytestLauncher(
        invoke_pytest=fake_main, error_stream=error_stream
    )

    assert launcher.run(args=["tests"], report_path=tmp_path / "summary.json") == 2
    assert "pytest returned an unexpected exit code: 42" in error_stream.getvalue()


@pytest.mark.parametrize("target", ["tests", "./tests", "./nested/../tests"])
def test_absent_default_tests_directory_is_an_empty_suite(
    target: str, run_launcher: Callable[[list[str]], int]
) -> None:
    assert run_launcher([target]) == 15


def test_absent_default_tests_directory_accepts_absolute_path(
    tmp_path: Path, run_launcher: Callable[[list[str]], int]
) -> None:
    assert run_launcher([str(tmp_path / "tests")]) == 15


def test_existing_empty_tests_directory_is_an_empty_suite(
    tmp_path: Path, run_launcher: Callable[[list[str]], int]
) -> None:
    (tmp_path / "tests").mkdir()

    assert run_launcher(["tests"]) == 15


@pytest.mark.parametrize(
    ("test_source", "exit_code", "summary"),
    [
        ("def test_example():\n    assert True\n", 10, "1 passed"),
        ("def test_example():\n    assert False\n", 11, "1 failed"),
        ("raise ImportError('broken collection')\n", 12, "broken collection"),
    ],
)
def test_existing_tests_run_with_native_output(
    test_source: str,
    exit_code: int,
    summary: str,
    tmp_path: Path,
    run_launcher: Callable[[list[str]], int],
    capsys: pytest.CaptureFixture[str],
) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_example.py").write_text(test_source)

    assert run_launcher(["tests"]) == exit_code
    assert summary in capsys.readouterr().out


def test_project_pytest_collection_settings_are_used(
    tmp_path: Path, run_launcher: Callable[[list[str]], int]
) -> None:
    (tmp_path / "pytest.ini").write_text("[pytest]\npython_files = example.py\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "example.py").write_text(
        "def test_example():\n    assert False\n"
    )

    assert run_launcher(["tests"]) == 11


def test_continuing_after_collection_errors_preserves_error_status(
    tmp_path: Path,
    run_launcher: Callable[[list[str]], int],
    capsys: pytest.CaptureFixture[str],
) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_example.py").write_text(
        "def test_example():\n    assert True\n"
    )
    (tmp_path / "tests" / "test_broken.py").write_text(
        "raise ImportError('broken collection')\n"
    )

    assert run_launcher(["tests", "--continue-on-collection-errors"]) == 12
    stdout = capsys.readouterr().out
    assert "broken collection" in stdout
    assert "1 passed, 1 error" in stdout


@pytest.mark.parametrize(
    "args", [["custom"], ["tests::test_example"], ["tests", "other"]]
)
def test_missing_custom_targets_keep_pytest_usage_errors(
    args: list[str], run_launcher: Callable[[list[str]], int]
) -> None:
    assert run_launcher(args) == 14


def test_pyargs_selection_is_not_treated_as_a_missing_directory(
    run_launcher: Callable[[list[str]], int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_find_spec = importlib.util.find_spec

    def find_spec(name: str) -> ModuleSpec | None:
        if name == "tests":
            return None
        return original_find_spec(name)

    monkeypatch.setattr(importlib.util, "find_spec", find_spec)

    assert run_launcher(["--pyargs", "tests"]) == 14


def test_addopts_additional_target_prevents_skipping_missing_default_tests(
    tmp_path: Path, run_launcher: Callable[[list[str]], int]
) -> None:
    (tmp_path / "pytest.ini").write_text("[pytest]\naddopts = missing_custom_target\n")

    assert run_launcher(["tests"]) == 14


def test_invalid_configuration_is_not_hidden_by_missing_tests(
    tmp_path: Path, run_launcher: Callable[[list[str]], int]
) -> None:
    (tmp_path / "pytest.ini").write_text("[pytest]\naddopts = --unknown-option\n")

    assert run_launcher(["tests"]) == 14


def test_broken_root_conftest_is_not_hidden_by_missing_tests(
    tmp_path: Path, run_launcher: Callable[[list[str]], int]
) -> None:
    (tmp_path / "conftest.py").write_text("raise ImportError('broken conftest')\n")

    assert run_launcher(["tests"]) == 14


def test_default_tests_target_cannot_be_a_file(
    tmp_path: Path,
    run_launcher: Callable[[list[str]], int],
    capsys: pytest.CaptureFixture[str],
) -> None:
    (tmp_path / "tests").write_text("")

    assert run_launcher(["tests"]) == 14
    assert "Default tests target must be a directory" in capsys.readouterr().err


def test_broken_default_tests_symlink_is_an_error(
    tmp_path: Path,
    run_launcher: Callable[[list[str]], int],
    capsys: pytest.CaptureFixture[str],
) -> None:
    (tmp_path / "tests").symlink_to(tmp_path / "missing")

    assert run_launcher(["tests"]) == 14
    assert "Cannot access default tests directory" in capsys.readouterr().err


def test_default_tests_symlink_to_a_directory_is_collected(
    tmp_path: Path, run_launcher: Callable[[list[str]], int]
) -> None:
    actual = tmp_path / "actual"
    actual.mkdir()
    (actual / "test_example.py").write_text("def test_example():\n    assert True\n")
    (tmp_path / "tests").symlink_to(actual, target_is_directory=True)

    assert run_launcher(["tests"]) == 10


def test_inaccessible_default_tests_path_is_an_error(
    tmp_path: Path,
    run_launcher: Callable[[list[str]], int],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    original_lstat = Path.lstat

    def inaccessible_lstat(path: Path) -> os.stat_result:
        if path == tmp_path / "tests":
            raise PermissionError("access denied")
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", inaccessible_lstat)

    assert run_launcher(["tests"]) == 14
    assert "access denied" in capsys.readouterr().err


def test_source_can_run_in_a_project_without_checksmith_installed(
    tmp_path: Path,
) -> None:
    launcher_path = Path(_pytest_launcher.__file__)
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_example.py").write_text(
        "import importlib.util\n"
        "def test_no_checksmith_dependency():\n"
        "    assert importlib.util.find_spec('checksmith') is None\n"
    )
    project_root = launcher_path.parents[2]
    source = (
        "import sys\n"
        f"sys.path = [path for path in sys.path if path != {str(project_root)!r}]\n"
        + launcher_path.read_text()
    )
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            source,
            str(tmp_path / "summary.json"),
            "tests",
            "-q",
            "-p",
            "no:cacheprovider",
        ],
        cwd=tmp_path,
        env=os.environ | {"PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "PYTEST_ADDOPTS": ""},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 10, result.stdout + result.stderr
    assert "1 passed" in result.stdout


class CoverageLauncher(Protocol):
    def __call__(
        self, *, extra_arguments: list[str], load_plugin: bool
    ) -> subprocess.CompletedProcess[str]: ...


@pytest.fixture
def run_coverage_launcher(
    tmp_path: Path,
) -> Iterator[CoverageLauncher]:
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    source = Path(_pytest_launcher.__file__).read_text(encoding="utf-8")
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("PYTEST_", "PYTHON", "COVERAGE_", "COV_CORE_"))
    }
    environment.update(
        {
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
            "COVERAGE_FILE": str(tmp_path / ".coverage"),
        }
    )
    with as_file(files("checksmith") / "assets" / "default") as assets:
        config = ConfigLoader(source=LocalYamlConfigSource()).load(
            config_file=assets / "checksmith.yaml",
            working_directory=assets,
        )
        check = next(check for check in config.checks if check.id == "pytest")

        def run(
            *, extra_arguments: list[str], load_plugin: bool
        ) -> subprocess.CompletedProcess[str]:
            plugin_arguments = ["-p", "pytest_cov.plugin"] if load_plugin else []
            arguments = check.arguments
            if any(
                argument.startswith("--cov-report=") for argument in extra_arguments
            ):
                arguments = tuple(
                    argument
                    for argument in arguments
                    if not argument.startswith("--cov-report=")
                )
            return subprocess.run(
                [
                    sys.executable,
                    "-c",
                    source,
                    str(tmp_path / "summary.json"),
                    *plugin_arguments,
                    "-p",
                    "no:cacheprovider",
                    "--import-mode=importlib",
                    *arguments,
                    *extra_arguments,
                ],
                cwd=tmp_path,
                env=environment,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
                timeout=20,
            )

        yield run


@pytest.mark.parametrize(
    ("called_functions", "exit_code", "percentage", "status"),
    [
        (7, 11, "85.00%", CheckStatus.FAILED),
        (8, 10, "90.00%", CheckStatus.PASSED),
        (9, 10, "95.00%", CheckStatus.PASSED),
    ],
)
def test_default_coverage_minimum_controls_the_check_result(
    command_factory: CommandFactory,
    tmp_path: Path,
    run_coverage_launcher: CoverageLauncher,
    called_functions: int,
    exit_code: int,
    percentage: str,
    status: CheckStatus,
) -> None:
    (tmp_path / "application.py").write_text(
        "\n".join(f"def action_{i}():\n    return {i}\n" for i in range(10)),
        encoding="utf-8",
    )
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_application.py").write_text(
        "import application\n\ndef test_actions():\n"
        + "\n".join(
            f"    assert application.action_{i}() == {i}"
            for i in range(called_functions)
        ),
        encoding="utf-8",
    )

    completed = run_coverage_launcher(extra_arguments=[], load_plugin=True)

    assert completed.returncode == exit_code, completed.stdout + completed.stderr
    result = command_factory.for_name(name=CommandName.PYTEST).process_response(
        check_id="pytest",
        report_path=tmp_path / "summary.json",
        exit_code=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )
    assert result.status is status
    assert f"Total coverage: {percentage}" in "\n".join(result.messages)
    assert "1 passed" in completed.stdout


def test_default_coverage_excludes_tests_and_counts_unimported_namespace_files(
    tmp_path: Path,
    run_coverage_launcher: CoverageLauncher,
) -> None:
    application = tmp_path / "application.py"
    application.write_text("VALUE = 1\n", encoding="utf-8")
    unimported = tmp_path / "src" / "unimported" / "module.py"
    unimported.parent.mkdir(parents=True)
    unimported.write_text("VALUE = 2\n", encoding="utf-8")
    excluded = (
        "tests/helper.py",
        "library/tests/helper.py",
        "test_helper.py",
        "helper_test.py",
        "library/test_helper.py",
        "library/helper_test.py",
        "conftest.py",
        "library/conftest.py",
        ".venv/ignored.py",
        "venv/ignored.py",
        "build/ignored.py",
        "dist/ignored.py",
        "nested/.venv/ignored.py",
    )
    for filename in excluded:
        path = tmp_path / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / "tests" / "test_application.py").write_text(
        "import application\n\ndef test_value():\n    assert application.VALUE == 1\n",
        encoding="utf-8",
    )

    completed = run_coverage_launcher(extra_arguments=[], load_plugin=True)

    assert completed.returncode == 11, completed.stdout + completed.stderr
    coverage = Coverage(data_file=str(tmp_path / ".coverage"), config_file=False)
    coverage.load()
    assert coverage.get_data().measured_files() == {str(application), str(unimported)}
    assert coverage.analysis2(str(unimported))[3] == [1]
    assert "Total coverage: 50.00%" in completed.stdout


def test_collected_tests_without_measurable_source_fail_coverage(
    tmp_path: Path,
    run_coverage_launcher: CoverageLauncher,
) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_example.py").write_text(
        "def test_example():\n    assert True\n", encoding="utf-8"
    )

    completed = run_coverage_launcher(extra_arguments=[], load_plugin=True)

    assert completed.returncode == 11, completed.stdout + completed.stderr
    assert "Total coverage: 0.00%" in completed.stdout


@pytest.mark.parametrize("tests_directory_exists", [False, True])
@pytest.mark.parametrize("source_exists", [False, True])
def test_empty_suites_are_exempt_from_the_default_coverage_minimum(
    command_factory: CommandFactory,
    tmp_path: Path,
    run_coverage_launcher: CoverageLauncher,
    tests_directory_exists: bool,
    source_exists: bool,
) -> None:
    if tests_directory_exists:
        (tmp_path / "tests").mkdir()
    if source_exists:
        (tmp_path / "application.py").write_text("VALUE = 1\n", encoding="utf-8")

    completed = run_coverage_launcher(extra_arguments=[], load_plugin=True)

    assert completed.returncode == 15, completed.stdout + completed.stderr
    result = command_factory.for_name(name=CommandName.PYTEST).process_response(
        check_id="pytest",
        report_path=tmp_path / "summary.json",
        exit_code=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )
    assert result.status is CheckStatus.PASSED
    assert "No tests were collected" in "\n".join(result.messages)
    assert "Coverage failure" not in completed.stdout
    assert "Required test coverage" not in completed.stdout


@pytest.mark.parametrize(
    ("problem", "diagnostic"),
    [
        ("missing_plugin", "unrecognized arguments"),
        ("invalid_pytest_config", "unrecognized arguments"),
        ("missing_coverage_config", "Couldn't read"),
    ],
)
def test_coverage_setup_errors_are_not_exempted_for_missing_tests(
    command_factory: CommandFactory,
    tmp_path: Path,
    run_coverage_launcher: CoverageLauncher,
    problem: str,
    diagnostic: str,
) -> None:
    extra_arguments: list[str] = []
    if problem == "invalid_pytest_config":
        (tmp_path / "pytest.ini").write_text(
            "[pytest]\naddopts = --unknown-option\n", encoding="utf-8"
        )
    elif problem == "missing_coverage_config":
        extra_arguments = ["--cov-config", str(tmp_path / "missing.toml")]

    completed = run_coverage_launcher(
        extra_arguments=extra_arguments, load_plugin=problem != "missing_plugin"
    )

    with pytest.raises(CheckOutputError, match=diagnostic):
        command_factory.for_name(name=CommandName.PYTEST).process_response(
            check_id="pytest",
            report_path=tmp_path / "summary.json",
            exit_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )


@pytest.mark.parametrize("continue_collection", [False, True])
def test_broken_collection_is_not_an_empty_suite_with_coverage(
    tmp_path: Path,
    run_coverage_launcher: CoverageLauncher,
    continue_collection: bool,
) -> None:
    (tmp_path / "application.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_broken.py").write_text(
        "raise ImportError('broken collection')\n", encoding="utf-8"
    )
    arguments = ["--continue-on-collection-errors"] if continue_collection else []

    completed = run_coverage_launcher(extra_arguments=arguments, load_plugin=True)

    assert completed.returncode == 12, completed.stdout + completed.stderr
    assert "broken collection" in completed.stdout
    if continue_collection:
        assert "Coverage failure" in completed.stdout


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        (["tests"], ["tests"]),
        (
            ["tests", "--cov-config", "/shared config/coverage.toml"],
            ["tests", "--cov-config=/shared config/coverage.toml"],
        ),
        (
            ["--cov-config=coverage.toml", "tests"],
            ["--cov-config=coverage.toml", "tests"],
        ),
        (["tests", "--cov-config"], ["tests", "--cov-config"]),
        (
            ["tests", "--cov-config", "--cov-report=term"],
            ["tests", "--cov-config", "--cov-report=term"],
        ),
        (
            ["tests", "--", "--cov-config", "coverage.toml"],
            ["tests", "--", "--cov-config", "coverage.toml"],
        ),
    ],
)
def test_coverage_config_paths_do_not_influence_pytest_project_discovery(
    arguments: list[str], expected: list[str], tmp_path: Path
) -> None:
    def fake_main(args: list[str], plugins: list[object]) -> int:
        assert args == expected
        return 0

    launcher = _pytest_launcher.PytestLauncher(
        invoke_pytest=fake_main, error_stream=StringIO()
    )

    assert launcher.run(args=arguments, report_path=tmp_path / "summary.json") == 10


def test_reusing_a_launcher_creates_fresh_collection_state(tmp_path: Path) -> None:
    seen: list[_pytest_launcher._ChecksmithPlugin] = []

    def invoke(args: list[str], plugins: list[object]) -> int:
        plugin = plugins[0]
        assert isinstance(plugin, _pytest_launcher._ChecksmithPlugin)
        assert plugin.collection_failed is False
        plugin.collection_failed = not seen
        seen.append(plugin)
        return 0

    launcher = _pytest_launcher.PytestLauncher(
        invoke_pytest=invoke, error_stream=StringIO()
    )

    assert launcher.run(args=["tests"], report_path=tmp_path / "summary.json") == 12
    assert launcher.run(args=["tests"], report_path=tmp_path / "summary.json") == 10
    assert seen[0] is not seen[1]


@pytest.mark.parametrize("quiet", [[], ["-qq"], ["-rN", "--tb=no"]])
def test_structured_summary_counts_outcomes_without_terminal_parsing(
    tmp_path: Path,
    run_launcher: Callable[[list[str]], int],
    quiet: list[str],
) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_outcomes.py").write_text(
        "import pytest, warnings\n"
        "def test_pass():\n    warnings.warn('test warning')\n"
        "def test_fail():\n    assert 1 == 2\n"
        "@pytest.mark.skip(reason='skip reason')\ndef test_skip():\n    pass\n"
        "@pytest.mark.xfail(reason='expected')\ndef test_xfail():\n    assert False\n"
        "@pytest.mark.xfail(reason='unexpected')\ndef test_xpass():\n    pass\n"
        "def test_deselected():\n    pass\n",
        encoding="utf-8",
    )
    assert run_launcher(["tests", "-k", "not deselected", *quiet]) == 11
    report = PytestSummary.model_validate_json((tmp_path / "summary.json").read_text())
    assert report.outcomes == {
        "passed": 1,
        "failed": 1,
        "skipped": 1,
        "xfailed": 1,
        "xpassed": 1,
    }
    assert report.warnings == 1
    assert report.deselected == 1
    assert report.duration_seconds > 0
    assert report.coverage is None
    assert len(report.failures) == 1
    assert report.failures[0].nodeid == "tests/test_outcomes.py::test_fail"
    assert report.failures[0].reason == "assert 1 == 2"


@pytest.mark.parametrize("phase", ["setup", "teardown"])
def test_fixture_errors_have_one_error_count_and_short_reason(
    tmp_path: Path,
    run_launcher: Callable[[list[str]], int],
    phase: str,
) -> None:
    (tmp_path / "tests").mkdir()
    body = "    raise RuntimeError('fixture broken')\n"
    if phase == "teardown":
        body = "    yield\n" + body
    (tmp_path / "tests" / "test_fixture.py").write_text(
        "import pytest\n@pytest.fixture\ndef fixture():\n"
        + body
        + "def test_fixture(fixture):\n    pass\n",
        encoding="utf-8",
    )
    assert run_launcher(["tests", "-qq"]) == 11
    report = PytestSummary.model_validate_json((tmp_path / "summary.json").read_text())
    assert report.outcomes == (
        {"error": 1} if phase == "setup" else {"passed": 1, "error": 1}
    )
    assert len(report.failures) == 1
    assert report.failures[0].phase == phase
    assert report.failures[0].reason == "RuntimeError: fixture broken"
    assert f"({phase}): RuntimeError: fixture broken" in report.messages()[1]


def test_launcher_rejects_relative_report_path_before_running_pytest() -> None:
    def never_called(args: list[str], plugins: list[object]) -> int:
        pytest.fail("pytest must not start with an invalid report path")

    launcher = _pytest_launcher.PytestLauncher(
        invoke_pytest=never_called, error_stream=StringIO()
    )
    with pytest.raises(ValueError, match="must be absolute"):
        launcher.run(args=[], report_path=Path("summary.json"))


def test_summary_collects_skips_during_collection(
    tmp_path: Path,
    run_launcher: Callable[[list[str]], int],
) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_skip.py").write_text(
        "import pytest\npytest.skip('module unavailable', allow_module_level=True)\n",
        encoding="utf-8",
    )
    assert run_launcher(["tests"]) == 15
    report = PytestSummary.model_validate_json((tmp_path / "summary.json").read_text())
    assert report.outcomes == {"skipped": 1}
    assert report.failures == ()


def test_coverage_summary_survives_disabled_terminal_reports(
    tmp_path: Path,
    run_coverage_launcher: CoverageLauncher,
) -> None:
    (tmp_path / "application.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_application.py").write_text(
        "import application\ndef test_value():\n    assert application.VALUE == 1\n",
        encoding="utf-8",
    )
    completed = run_coverage_launcher(
        extra_arguments=["--cov-report=", "--cov-fail-under=0"],
        load_plugin=True,
    )
    assert completed.returncode == 10, completed.stdout + completed.stderr
    report = PytestSummary.model_validate_json((tmp_path / "summary.json").read_text())
    assert report.coverage is not None
    assert report.coverage.total == 100.0
    assert report.coverage.minimum == 0.0
    assert "Total coverage" not in completed.stdout
    assert "Total coverage: 100.00% (required: 0.00%)" in report.messages()


def test_disabling_coverage_omits_summary_coverage(
    tmp_path: Path,
    run_coverage_launcher: CoverageLauncher,
) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_application.py").write_text(
        "def test_value():\n    assert True\n",
        encoding="utf-8",
    )
    completed = run_coverage_launcher(extra_arguments=["--no-cov"], load_plugin=True)
    assert completed.returncode == 10, completed.stdout + completed.stderr
    report = PytestSummary.model_validate_json((tmp_path / "summary.json").read_text())
    assert report.coverage is None


def test_empty_suite_passes_when_coverage_reports_are_disabled(
    tmp_path: Path,
    run_coverage_launcher: CoverageLauncher,
) -> None:
    completed = run_coverage_launcher(
        extra_arguments=["--cov-report=", "--cov-fail-under=0"],
        load_plugin=True,
    )
    assert completed.returncode == 15, completed.stdout + completed.stderr
    report = PytestSummary.model_validate_json((tmp_path / "summary.json").read_text())
    assert report.coverage is not None
    assert report.coverage.total is None
    assert report.outcomes == {}


def test_failed_tests_keep_coverage_total_when_failure_reports_are_suppressed(
    tmp_path: Path,
    run_coverage_launcher: CoverageLauncher,
) -> None:
    (tmp_path / "application.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_application.py").write_text(
        "import application\ndef test_value():\n    assert application.VALUE == 2\n",
        encoding="utf-8",
    )
    completed = run_coverage_launcher(
        extra_arguments=["--no-cov-on-fail"], load_plugin=True
    )
    assert completed.returncode == 11, completed.stdout + completed.stderr
    report = PytestSummary.model_validate_json((tmp_path / "summary.json").read_text())
    assert report.coverage is not None
    assert report.coverage.total == 100.0
    assert report.outcomes == {"failed": 1}
    assert "Total coverage" not in completed.stdout
