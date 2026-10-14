import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from services.habit_tracker.main import create_app
from services.habit_tracker.state_probe import fingerprint
from tests.platform_fixture import fixture

ALEX = {"X-Platform-User-Context": "member@example.test"}


def populate(database: Path) -> None:
    with (
        fixture() as core,
        TestClient(
            create_app(database, platform=core.platform), headers=ALEX
        ) as browser,
    ):
        for method, path, payload in (
            ("PUT", "/api/water/settings", {"daily_goal_ml": 2600}),
            ("POST", "/api/water/drinks", {"amount_ml": 250, "drink_type": "tea"}),
            ("PUT", "/api/budget/daily-budget", {"amount_cents": 1500}),
            ("PUT", "/api/budget/savings-goal", {"target_cents": 50000}),
            (
                "POST",
                "/api/budget/transactions",
                {"kind": "daily_spend", "amount_cents": 700, "category": "food"},
            ),
            (
                "POST",
                "/api/budget/transactions",
                {"kind": "fund_contribution", "amount_cents": 2000},
            ),
            (
                "POST",
                "/api/study/sessions",
                {"activity": "Reading", "duration_minutes": 25},
            ),
        ):
            assert browser.request(method, path, json=payload).status_code < 300
        session = browser.get("/api/study/summary").json()["active"]["id"]
        assert browser.post(f"/api/study/sessions/{session}/stop").status_code == 200


def test_repeat_startup_preserves_every_record_schema_and_sequence(
    tmp_path: Path,
) -> None:
    database = tmp_path / "habit-tracker.db"
    populate(database)
    before = fingerprint(database)
    assert all(
        table["rows"] > 0
        for name, table in before["tables"].items()  # type: ignore[attr-defined]
        if name != "sqlite_sequence"
    )
    with fixture() as core, TestClient(create_app(database, platform=core.platform)):
        pass
    assert fingerprint(database) == before
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE user_settings SET daily_goal_ml = 2601")
    assert fingerprint(database) != before


def test_probe_is_read_only_and_missing_file_is_not_created(tmp_path: Path) -> None:
    missing = tmp_path / "missing.db"
    with pytest.raises(sqlite3.OperationalError):
        fingerprint(missing)
    assert not missing.exists()
