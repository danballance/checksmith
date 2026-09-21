"""Tests for the starter resources bundled with the package.

These load the shipped files through :mod:`importlib.resources` and the real
loader, so a template that could not actually validate fails here rather than in
somebody's project.
"""

import os
import subprocess
import sys
import tomllib
from collections.abc import Iterator
from importlib.resources import as_file, files
from pathlib import Path
from textwrap import dedent

import pytest
import yaml
from pydantic import BaseModel, ConfigDict

from checksmith.commands.semgrep import SemgrepReport
from checksmith.config import Config, ConfigPath
from checksmith.dtos import CommandName, PackageType


@pytest.fixture(scope="module")
def default_assets() -> Iterator[Path]:
    """The starter directory, located without assuming a source checkout."""
    resource = files("checksmith") / "assets" / "default"
    with as_file(resource) as path:
        yield path


def test_the_starter_directory_ships_three_files(default_assets: Path) -> None:
    """Dot-entries are skipped: Ruff plants a cache beside any config it finds."""
    shipped = sorted(
        item.name
        for item in default_assets.iterdir()
        if not item.name.startswith(".")
    )

    assert shipped == ["checksmith.yaml", "ruff.toml", "semgrep.yaml"]


def test_the_starter_config_loads_through_the_real_loader(
    default_assets: Path,
) -> None:
    config = Config.from_path(
        config_file=default_assets / "checksmith.yaml",
        working_directory=default_assets,
    )

    # ``project_root: ../../..`` resolves against the config directory.
    assert config.project_root == default_assets.parents[2]
    assert tuple(check.id for check in config.checks) == ("ruff", "semgrep")


def test_the_starter_config_references_only_files_it_ships_with(
    default_assets: Path,
) -> None:
    """A wheel whose packaged data stopped matching would ship a config
    pointing at a file that is not in the distribution.

    The loader resolves a ``config_path`` argument, so the test reads the answer
    rather than recomputing it: what it checks is the path the tool is handed.
    """
    config = Config.from_path(
        config_file=default_assets / "checksmith.yaml",
        working_directory=default_assets,
    )

    for check, filename in zip(
        config.checks, ("ruff.toml", "semgrep.yaml"), strict=True
    ):
        paths = tuple(
            argument.config_path
            for argument in check.args
            if isinstance(argument, ConfigPath)
        )

        assert paths == (default_assets / filename,)
        assert paths[0].is_file()


def test_the_starter_config_builds_a_ruff_invocation(
    default_assets: Path,
) -> None:
    config = Config.from_path(
        config_file=default_assets / "checksmith.yaml",
        working_directory=default_assets,
    )

    assert config.checks[0].argv == (
        "uvx",
        "--from",
        "ruff==0.16.7",
        "ruff",
        "check",
        "--config",
        # Absolute, and so independent of the project root the tool runs in.
        str(default_assets / "ruff.toml"),
        "--output-format",
        "json",
        # Relative, and so read by ruff against that project root.
        ".",
    )


def test_the_starter_config_builds_a_semgrep_invocation(
    default_assets: Path,
) -> None:
    config = Config.from_path(
        config_file=default_assets / "checksmith.yaml",
        working_directory=default_assets,
    )

    assert config.checks[1].argv == (
        "uvx",
        "--from",
        "semgrep==1.176.1",
        "semgrep",
        "scan",
        "--config",
        str(default_assets / "semgrep.yaml"),
        "--json",
        "--error",
        "--strict",
        ".",
    )


def test_the_starter_config_asks_for_the_output_its_command_reads(
    default_assets: Path,
) -> None:
    """A config must name a command whose adapter reads what its args produce.

    Checksmith appends nothing to the vector, so the starter has to carry the
    switch itself, and this is what pairs it with :class:`RuffCommand`.
    """
    config = Config.from_path(
        config_file=default_assets / "checksmith.yaml",
        working_directory=default_assets,
    )

    assert config.checks[0].command is CommandName.RUFF
    assert "--output-format" in config.checks[0].args
    assert "json" in config.checks[0].args
    assert config.checks[1].command is CommandName.SEMGREP
    assert "--json" in config.checks[1].args


def test_the_starter_checks_use_the_python_package_type(default_assets: Path) -> None:
    config = Config.from_path(
        config_file=default_assets / "checksmith.yaml",
        working_directory=default_assets,
    )

    assert all(check.package_type is PackageType.UVX for check in config.checks)


def test_the_starter_ruff_configuration_is_a_standalone_one(
    default_assets: Path,
) -> None:
    """A standalone file uses ``[lint]``; ``[tool.ruff.lint]`` is pyproject's."""
    settings = tomllib.loads(
        (default_assets / "ruff.toml").read_text(encoding="utf-8")
    )

    assert "lint" in settings
    assert "tool" not in settings


def test_the_starter_ruff_configuration_selects_its_rules_explicitly(
    default_assets: Path,
) -> None:
    """An upgrade must not be able to change what this gate enforces."""
    settings = tomllib.loads(
        (default_assets / "ruff.toml").read_text(encoding="utf-8")
    )

    assert settings["lint"]["select"] != []
    assert settings["target-version"] == "py312"


def test_the_starter_semgrep_configuration_enforces_the_supplied_rules(
    default_assets: Path,
) -> None:
    settings = yaml.safe_load(
        (default_assets / "semgrep.yaml").read_text(encoding="utf-8")
    )
    keyword_rule, variadic_rule, import_rule = settings["rules"]

    assert keyword_rule["id"] == "python-require-keyword-only-parameters"
    assert variadic_rule["id"] == "python-no-variadic-parameters"
    assert import_rule["id"] == "python-imports-at-top"
    assert all(rule["languages"] == ["python"] for rule in settings["rules"])
    assert all(rule["severity"] == "ERROR" for rule in settings["rules"])
    assert keyword_rule["message"] == (
        "Declare parameters after a bare * so callers must pass them "
        "by name. Only self or cls may precede the *."
    )
    assert variadic_rule["message"] == (
        "Replace variadic parameters with explicitly named parameters. "
        "Neither *args nor **kwargs is permitted."
    )
    assert import_rule["message"] == (
        "Place imports at the top of the module before other code. "
        "Only top-of-module TYPE_CHECKING import blocks are permitted."
    )
    assert keyword_rule["patterns"] == [
        {"pattern": "def $FUNC(...):\n    ...\n"},
        {"pattern-not": "def $FUNC(*, ...):\n    ...\n"},
        {"pattern-not": "def $FUNC(self, *, ...):\n    ...\n"},
        {"pattern-not": "def $FUNC(cls, *, ...):\n    ...\n"},
        {"pattern-not": "def $FUNC():\n    ...\n"},
        {"pattern-not": "def $FUNC(self):\n    ...\n"},
        {"pattern-not": "def $FUNC(cls):\n    ...\n"},
    ]
    assert variadic_rule["pattern-either"] == [
        {"pattern": "def $FUNC(..., *$ARGS, ...):\n    ...\n"},
        {"pattern": "def $FUNC(..., **$KWARGS):\n    ...\n"},
    ]


class ImportPlacementCase(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    source: str
    expected_lines: frozenset[int]


IMPORT_PLACEMENT_CASES = (
    ImportPlacementCase(name="empty", source="", expected_lines=frozenset()),
    ImportPlacementCase(
        name="without_imports",
        source="VALUE = 1\ndef run():\n    return VALUE\n",
        expected_lines=frozenset(),
    ),
    ImportPlacementCase(
        name="initial_import_forms",
        source="""\
            import os
            import sys as system
            import pathlib, collections
            import xml.etree.ElementTree as etree
            from pathlib import Path
            from collections import deque as queue
            from . import sibling
            from ..parent import name
            from package import *
            from package import (
                first,
                second as renamed,
            )
            VALUE = 1
        """,
        expected_lines=frozenset(),
    ),
    ImportPlacementCase(
        name="docstring_comments_and_future_import",
        source='''\
            #!/usr/bin/env python
            # Module comment.
            """Module documentation.

            import example
            """
            from __future__ import annotations
            # Another import group.
            import os
            VALUE = 1
        ''',
        expected_lines=frozenset(),
    ),
    ImportPlacementCase(
        name="concatenated_docstring",
        source='("Module " "documentation.")\nimport os\n',
        expected_lines=frozenset(),
    ),
    ImportPlacementCase(
        name="raw_docstring",
        source='r"Module documentation."\nimport os\n',
        expected_lines=frozenset(),
    ),
    ImportPlacementCase(
        name="uppercase_raw_docstring",
        source='R"Module documentation."\nimport os\n',
        expected_lines=frozenset(),
    ),
    ImportPlacementCase(
        name="uppercase_unicode_docstring",
        source='U"Module documentation."\nimport os\n',
        expected_lines=frozenset(),
    ),
    ImportPlacementCase(
        name="import_text_in_strings",
        source='''\
            import os
            EXAMPLE = """
            import sys
            from pathlib import Path
            """
        ''',
        expected_lines=frozenset(),
    ),
    ImportPlacementCase(
        name="type_checking_direct",
        source="""\
            from typing import TYPE_CHECKING
            if TYPE_CHECKING:
                import package
                from package import Model
            import os
            VALUE = 1
        """,
        expected_lines=frozenset(),
    ),
    ImportPlacementCase(
        name="type_checking_qualified",
        source="import typing\nif typing.TYPE_CHECKING:\n    import package\n",
        expected_lines=frozenset(),
    ),
    ImportPlacementCase(
        name="type_checking_module_alias",
        source="import typing as t\nif t.TYPE_CHECKING:\n    import package\n",
        expected_lines=frozenset(),
    ),
    ImportPlacementCase(
        name="type_checking_name_alias",
        source="from typing import TYPE_CHECKING as TC\nif TC:\n    import package\n",
        expected_lines=frozenset(),
    ),
    ImportPlacementCase(
        name="type_checking_parenthesized",
        source="from typing import TYPE_CHECKING\nif (TYPE_CHECKING):\n    import package\n",
        expected_lines=frozenset(),
    ),
    ImportPlacementCase(
        name="type_checking_inline",
        source="from typing import TYPE_CHECKING\nif TYPE_CHECKING: import package\n",
        expected_lines=frozenset(),
    ),
    ImportPlacementCase(
        name="multiple_type_checking_blocks",
        source="""\
            from typing import TYPE_CHECKING
            if TYPE_CHECKING:
                import first
            import os
            if TYPE_CHECKING:
                from second import Model
            VALUE = 1
        """,
        expected_lines=frozenset(),
    ),
    ImportPlacementCase(
        name="after_assignment",
        source="VALUE = 1\nimport os\nfrom pathlib import Path\n",
        expected_lines=frozenset({2, 3}),
    ),
    ImportPlacementCase(
        name="after_annotated_assignment",
        source="VALUE: int\nimport os\n",
        expected_lines=frozenset({2}),
    ),
    ImportPlacementCase(
        name="after_call",
        source="initialize()\nimport os\n",
        expected_lines=frozenset({2}),
    ),
    ImportPlacementCase(
        name="after_function",
        source="def run():\n    pass\nimport os\n",
        expected_lines=frozenset({3}),
    ),
    ImportPlacementCase(
        name="after_class",
        source="class Model:\n    pass\nimport os\n",
        expected_lines=frozenset({3}),
    ),
    ImportPlacementCase(
        name="after_function_with_import",
        source="def run():\n    import os\nimport sys\n",
        expected_lines=frozenset({2, 3}),
    ),
    ImportPlacementCase(
        name="after_class_with_import",
        source="class Model:\n    import os\nimport sys\n",
        expected_lines=frozenset({2, 3}),
    ),
    ImportPlacementCase(
        name="after_decorated_function",
        source="@decorate\ndef run():\n    pass\nimport os\n",
        expected_lines=frozenset({4}),
    ),
    ImportPlacementCase(
        name="after_pass",
        source="pass\nimport os\n",
        expected_lines=frozenset({2}),
    ),
    ImportPlacementCase(
        name="after_assertion",
        source="assert ready\nimport os\n",
        expected_lines=frozenset({2}),
    ),
    ImportPlacementCase(
        name="after_type_alias",
        source="type Identifier = int\nimport os\n",
        expected_lines=frozenset({2}),
    ),
    ImportPlacementCase(
        name="after_non_docstring_string",
        source='import os\n"This is not a module docstring."\nimport sys\n',
        expected_lines=frozenset({3}),
    ),
    ImportPlacementCase(
        name="after_second_string",
        source='"Module documentation."\n"Another statement."\nimport os\n',
        expected_lines=frozenset({3}),
    ),
    ImportPlacementCase(
        name="after_bytes_literal",
        source='b"Not a docstring."\nimport os\n',
        expected_lines=frozenset({2}),
    ),
    ImportPlacementCase(
        name="after_formatted_string",
        source='f"Not a docstring: {name}"\nimport os\n',
        expected_lines=frozenset({2}),
    ),
    ImportPlacementCase(
        name="after_string_assignment",
        source='MESSAGE = "Import documentation."\nimport os\n',
        expected_lines=frozenset({2}),
    ),
    ImportPlacementCase(
        name="late_multiline_import",
        source="VALUE = 1\nfrom package import (\n    first,\n    second,\n)\n",
        expected_lines=frozenset({2}),
    ),
    ImportPlacementCase(
        name="late_relative_and_wildcard_imports",
        source="VALUE = 1\nfrom . import sibling\nfrom package import *\n",
        expected_lines=frozenset({2, 3}),
    ),
    ImportPlacementCase(
        name="semicolon_after_code",
        source="VALUE = 1; import os\n",
        expected_lines=frozenset({1}),
    ),
    ImportPlacementCase(
        name="comments_do_not_restart_import_section",
        source="VALUE = 1\n\n# New group.\nimport os\n",
        expected_lines=frozenset({4}),
    ),
    ImportPlacementCase(
        name="function_imports",
        source="def run():\n    import os\n    from pathlib import Path\n",
        expected_lines=frozenset({2, 3}),
    ),
    ImportPlacementCase(
        name="async_function_import",
        source="async def run():\n    import os\n",
        expected_lines=frozenset({2}),
    ),
    ImportPlacementCase(
        name="inline_function_import",
        source="def run(): import os\n",
        expected_lines=frozenset({1}),
    ),
    ImportPlacementCase(
        name="class_and_method_imports",
        source="class Model:\n    import os\n    def run(self):\n        import sys\n",
        expected_lines=frozenset({2, 4}),
    ),
    ImportPlacementCase(
        name="conditional_imports",
        source="if enabled:\n    import os\nelse:\n    import sys\n",
        expected_lines=frozenset({2, 4}),
    ),
    ImportPlacementCase(
        name="for_import",
        source="for item in items:\n    import os\n",
        expected_lines=frozenset({2}),
    ),
    ImportPlacementCase(
        name="while_import",
        source="while enabled:\n    import os\n",
        expected_lines=frozenset({2}),
    ),
    ImportPlacementCase(
        name="with_import",
        source="with resource():\n    import os\n",
        expected_lines=frozenset({2}),
    ),
    ImportPlacementCase(
        name="try_imports",
        source="""\
            try:
                import first
            except ImportError:
                import second
            else:
                import third
            finally:
                import fourth
        """,
        expected_lines=frozenset({2, 4, 6, 8}),
    ),
    ImportPlacementCase(
        name="match_import",
        source="match value:\n    case 1:\n        import os\n",
        expected_lines=frozenset({3}),
    ),
    ImportPlacementCase(
        name="function_type_checking_import",
        source="""\
            from typing import TYPE_CHECKING
            def run():
                if TYPE_CHECKING:
                    import package
        """,
        expected_lines=frozenset({4}),
    ),
    ImportPlacementCase(
        name="class_type_checking_import",
        source="""\
            from typing import TYPE_CHECKING
            class Model:
                if TYPE_CHECKING:
                    import package
        """,
        expected_lines=frozenset({4}),
    ),
    ImportPlacementCase(
        name="conditional_type_checking_import",
        source="""\
            from typing import TYPE_CHECKING
            if enabled:
                if TYPE_CHECKING:
                    import package
        """,
        expected_lines=frozenset({4}),
    ),
    ImportPlacementCase(
        name="type_checking_nested_import",
        source="""\
            from typing import TYPE_CHECKING
            if TYPE_CHECKING:
                if enabled:
                    import package
        """,
        expected_lines=frozenset({4}),
    ),
    ImportPlacementCase(
        name="type_checking_else_import",
        source="""\
            from typing import TYPE_CHECKING
            if TYPE_CHECKING:
                import package
            else:
                import runtime
        """,
        expected_lines=frozenset({5}),
    ),
    ImportPlacementCase(
        name="type_checking_elif_import",
        source="""\
            from typing import TYPE_CHECKING
            if TYPE_CHECKING:
                import package
            elif enabled:
                import runtime
        """,
        expected_lines=frozenset({5}),
    ),
    ImportPlacementCase(
        name="type_checking_after_assignment",
        source="""\
            from typing import TYPE_CHECKING
            VALUE = 1
            if TYPE_CHECKING:
                import package
        """,
        expected_lines=frozenset({4}),
    ),
    ImportPlacementCase(
        name="type_checking_after_function",
        source="""\
            from typing import TYPE_CHECKING
            def run():
                pass
            if TYPE_CHECKING:
                import package
        """,
        expected_lines=frozenset({5}),
    ),
    ImportPlacementCase(
        name="type_checking_import_after_guard_code",
        source="""\
            from typing import TYPE_CHECKING
            if TYPE_CHECKING:
                VALUE = 1
                import package
        """,
        expected_lines=frozenset({4}),
    ),
    ImportPlacementCase(
        name="type_checking_combined_condition",
        source="""\
            from typing import TYPE_CHECKING
            if TYPE_CHECKING and enabled:
                import package
        """,
        expected_lines=frozenset({3}),
    ),
    ImportPlacementCase(
        name="after_dunder_assignment",
        source='__all__ = ["run"]\nimport os\n',
        expected_lines=frozenset({2}),
    ),
    ImportPlacementCase(
        name="nested_type_checking_guards",
        source="""\
            from typing import TYPE_CHECKING
            if TYPE_CHECKING:
                import allowed
                if TYPE_CHECKING:
                    import nested
        """,
        expected_lines=frozenset({5}),
    ),
    ImportPlacementCase(
        name="after_type_checking_else_code",
        source="""\
            from typing import TYPE_CHECKING
            if TYPE_CHECKING:
                import allowed
            else:
                VALUE = 1
            import late
        """,
        expected_lines=frozenset({6}),
    ),
    ImportPlacementCase(
        name="after_type_checking_body_code",
        source="""\
            from typing import TYPE_CHECKING
            if TYPE_CHECKING:
                import allowed
                VALUE = 1
            import late
        """,
        expected_lines=frozenset({5}),
    ),
    ImportPlacementCase(
        name="after_string_method_call",
        source='"Not a docstring.".format()\nimport os\n',
        expected_lines=frozenset({2}),
    ),
    ImportPlacementCase(
        name="after_noninitial_raw_string",
        source='import os\nr"Not a docstring."\nimport sys\n',
        expected_lines=frozenset({3}),
    ),
    ImportPlacementCase(
        name="after_noninitial_concatenated_string",
        source='import os\n("Not a " "docstring.")\nimport sys\n',
        expected_lines=frozenset({3}),
    ),
    ImportPlacementCase(
        name="async_with_import",
        source="async def run():\n    async with resource():\n        import os\n",
        expected_lines=frozenset({3}),
    ),
    ImportPlacementCase(
        name="async_for_import",
        source="async def run():\n    async for item in items:\n        import os\n",
        expected_lines=frozenset({3}),
    ),
    ImportPlacementCase(
        name="unicode_docstring",
        source='u"Module documentation: λ."\nimport os\n',
        expected_lines=frozenset(),
    ),
    ImportPlacementCase(
        name="multiline_parenthesized_docstring",
        source='(\n    "Module "\n    "documentation."\n)\nimport os\n',
        expected_lines=frozenset(),
    ),
    ImportPlacementCase(
        name="same_line_docstring_and_import",
        source='"Module documentation."; import os\n',
        expected_lines=frozenset(),
    ),
    ImportPlacementCase(
        name="same_line_noninitial_string_and_import",
        source='import os; "Not a docstring."; import sys\n',
        expected_lines=frozenset({1}),
    ),
    ImportPlacementCase(
        name="multiple_type_checking_guards_with_else_branches",
        source="""\
            from typing import TYPE_CHECKING
            if TYPE_CHECKING:
                import first
            else:
                initialize()
            if TYPE_CHECKING:
                import late
            else:
                import runtime
            import outer
        """,
        expected_lines=frozenset({7, 9, 10}),
    ),
    ImportPlacementCase(
        name="concatenated_prefixed_docstring",
        source='(\n    R"doc"\n    u"and more"\n)\nimport os\n',
        expected_lines=frozenset(),
    ),
    ImportPlacementCase(
        name="after_concatenated_string_method_call",
        source='("doc" "more").upper()\nimport os\n',
        expected_lines=frozenset({2}),
    ),
    ImportPlacementCase(
        name="after_concatenated_bytes_literals",
        source='(\n    B"doc"\n    br"and more"\n)\nimport os\n',
        expected_lines=frozenset({5}),
    ),
    ImportPlacementCase(
        name="after_concatenated_formatted_string",
        source='"doc" RF"{value}"\nimport os\n',
        expected_lines=frozenset({2}),
    ),
)


@pytest.fixture(scope="module")
def import_placement_report(
    default_assets: Path,
    tmp_path_factory: pytest.TempPathFactory,
) -> SemgrepReport:
    working_directory = tmp_path_factory.mktemp("semgrep_imports")
    sources_directory = working_directory / "sources"
    sources_directory.mkdir()
    for case in IMPORT_PLACEMENT_CASES:
        (sources_directory / f"{case.name}.py").write_text(
            dedent(case.source), encoding="utf-8"
        )
    completed = subprocess.run(
        [
            str(Path(sys.executable).with_name("semgrep")),
            "scan",
            "--config",
            str(default_assets / "semgrep.yaml"),
            "--json",
            "--strict",
            "--metrics=off",
            "--disable-version-check",
            "--no-git-ignore",
            "--no-rewrite-rule-ids",
            "--jobs=1",
            str(sources_directory),
        ],
        cwd=working_directory,
        env=os.environ
        | {
            "SEMGREP_SETTINGS_FILE": str(working_directory / "settings.yaml"),
            "SEMGREP_LOG_FILE": str(working_directory / "semgrep.log"),
        },
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout
    report = SemgrepReport.model_validate_json(completed.stdout)
    assert report.errors == (), completed.stderr
    return report


@pytest.mark.parametrize(
    "case", IMPORT_PLACEMENT_CASES, ids=[case.name for case in IMPORT_PLACEMENT_CASES]
)
def test_the_starter_semgrep_rule_enforces_import_placement(
    case: ImportPlacementCase,
    import_placement_report: SemgrepReport,
) -> None:
    actual_lines = frozenset(
        finding.start.line
        for finding in import_placement_report.results
        if finding.check_id == "python-imports-at-top"
        and finding.path.name == f"{case.name}.py"
    )

    assert actual_lines == case.expected_lines


@pytest.mark.parametrize(
    ("case_name", "expected_column"),
    [
        ("semicolon_after_code", 12),
        ("inline_function_import", 12),
        ("same_line_noninitial_string_and_import", 32),
    ],
)
def test_same_line_import_findings_identify_only_the_misplaced_import(
    case_name: str,
    expected_column: int,
    import_placement_report: SemgrepReport,
) -> None:
    actual_columns = tuple(
        finding.start.col
        for finding in import_placement_report.results
        if finding.check_id == "python-imports-at-top"
        and finding.path.name == f"{case_name}.py"
    )

    assert actual_columns == (expected_column,)
