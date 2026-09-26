from pathlib import Path

from rich.console import Console

from checksmith.dtos import ExitCode
from checksmith.outputs.initoutput import InitOutput


def test_init_output_reports_the_root_and_created_files() -> None:
    output = InitOutput(
        project_root=Path("/project/[bold]"),
        created_files=(
            Path("/settings/checksmith.yaml"),
            Path("/settings/ruff.toml"),
        ),
    )
    console = Console(width=120, color_system=None)

    with console.capture() as capture:
        console.print(output)

    assert capture.get() == (
        "Project root: /project/[bold]\n"
        "Created:\n"
        "  /settings/checksmith.yaml\n"
        "  /settings/ruff.toml\n"
    )
    assert output.exit_code is ExitCode.SUCCESS


def test_init_output_serializes_paths() -> None:
    output = InitOutput(
        project_root=Path("/project"),
        created_files=(Path("/settings/checksmith.yaml"),),
    )

    assert output.model_dump(mode="json") == {
        "project_root": "/project",
        "created_files": ["/settings/checksmith.yaml"],
    }
