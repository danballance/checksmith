from pathlib import Path


class Config:
    def __init__(self, path: Path) -> None:
        self.path = path
