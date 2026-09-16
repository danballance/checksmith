"""Building the tools a run will execute from a resolved configuration."""

from typing import Protocol

from checksmith.config import Config
from checksmith.tools.process_spec import ProcessSpec
from checksmith.tools.tool import ProcessTool, Tool


class ToolSource(Protocol):
    """Somewhere the tools for a run come from."""

    def get_tools(self) -> tuple[Tool, ...]:
        """Every tool this run should execute, in the order they will run."""
        ...


class ToolFactory:
    """Turns a resolved configuration into one tool per configured check.

    A plain class rather than a Pydantic model, like :class:`Runner`: it is a
    service, so there is no data to validate and nothing to serialise.
    """

    def __init__(self, config: Config) -> None:
        self.config = config

    def get_tools(self) -> tuple[ProcessTool, ...]:
        """Build every configured tool, in the order the config file declares them.

        Narrower than :class:`ToolSource` asks for, which still satisfies it:
        every check Checksmith knows about today runs a process, and saying so
        costs a caller nothing.
        """
        return tuple(
            ProcessTool(
                name=check.id,
                spec=ProcessSpec.from_check(
                    check=check, project_root=self.config.project_root
                ),
            )
            for check in self.config.checks
        )
