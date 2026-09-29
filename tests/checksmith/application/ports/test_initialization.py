from pathlib import Path

import pytest
from pydantic import ValidationError

from checksmith.application.ports.initialization import PreparedFile


def test_prepared_files_are_immutable(tmp_path: Path) -> None:
    prepared = PreparedFile(path=tmp_path / "file", content=b"original")

    with pytest.raises(ValidationError, match="frozen"):
        prepared.content = b"changed"  # ty: ignore[invalid-assignment]
    assert prepared.content == b"original"
