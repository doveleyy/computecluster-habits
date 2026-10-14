import pytest


@pytest.fixture(autouse=True)
def household_timezone(monkeypatch: pytest.MonkeyPatch) -> None:
    # The tests fix their expected local days in the household's timezone.
    monkeypatch.setenv("TZ", "Asia/Singapore")
