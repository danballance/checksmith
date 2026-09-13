from checksmith.config import Config
from checksmith.tools.tool import Tool


class ToolFactory:
    def __init__(self, config: Config) -> None:
        self.config = config

    def get_tools(self) -> list[Tool]:
        return []
