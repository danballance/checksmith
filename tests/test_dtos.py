import pytest
from pydantic import ValidationError

from checksmith.dtos import CheckResult, CheckStatus


def test_check_result_requires_an_explicit_status() -> None:
    with pytest.raises(ValidationError) as raised:
        CheckResult.model_validate({"check_id": "lint", "messages": []})

    assert raised.value.errors()[0]["loc"] == ("status",)
    assert raised.value.errors()[0]["type"] == "missing"


@pytest.mark.parametrize("status", [None, "failure", "unknown"])
def test_check_result_rejects_invalid_statuses(status: str | None) -> None:
    with pytest.raises(ValidationError) as raised:
        CheckResult.model_validate(
            {"check_id": "lint", "status": status, "messages": []}
        )

    assert raised.value.errors()[0]["loc"] == ("status",)


@pytest.mark.parametrize("status", list(CheckStatus))
def test_check_result_accepts_each_explicit_status(status: CheckStatus) -> None:
    result = CheckResult.model_validate(
        {"check_id": "lint", "status": status.value, "messages": []}
    )

    assert result.status is status
