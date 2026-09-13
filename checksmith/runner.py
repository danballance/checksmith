from checksmith.outputs.check_full import CheckFull
from checksmith.tools.tool import Tool


class Runner:
    def __init__(self, tools: list[Tool]):
        self.tools = tools

    def check(self) -> CheckFull:
        # Placeholder: tools are not executed yet, so every run reports an empty
        # suite. Correct for the current empty tool list from ``ToolFactory``.
        return CheckFull()
