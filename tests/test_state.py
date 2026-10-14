import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from services.habit_tracker import state_probe
from services.habit_tracker.main import create_app
from services.habit_tracker.state_probe import TABLES, Table, fingerprint
from tests.platform_fixture import fixture

ALEX = {"X-Platform-User-Context": "member@example.test"}

# The live database's layout, where ALTER TABLE appended later columns.
LIVE_TABLES = {
    "drinks": """
        CREATE TABLE drinks (
            id TEXT PRIMARY KEY,
            owner_identity TEXT NOT NULL REFERENCES users(identity),
            amount_ml INTEGER NOT NULL CHECK (amount_ml BETWEEN 10 AND 2000),
            consumed_at TEXT NOT NULL
        , drink_type TEXT NOT NULL DEFAULT 'water',
          temperature TEXT NOT NULL DEFAULT 'normal',
          sweetness TEXT, recorded_at TEXT)
    """,
    "budget_transactions": """
        CREATE TABLE budget_transactions (
            id TEXT PRIMARY KEY,
            owner_identity TEXT NOT NULL REFERENCES users(identity),
            kind TEXT NOT NULL,
            amount_cents INTEGER NOT NULL
                CHECK (amount_cents BETWEEN 1 AND 100000000),
            description TEXT NOT NULL,
            occurred_at TEXT NOT NULL
        , category TEXT NOT NULL DEFAULT 'other', recorded_at TEXT)
    """,
}


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
            (
                "POST",
                "/api/water/drinks",
                {
                    "amount_ml": 300,
                    "drink_type": "coffee",
                    "temperature": "iced",
                    "sweetness": "less",
                },
            ),
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


def populated(tmp_path: Path) -> Path:
    database = tmp_path / "habit-tracker.db"
    populate(database)
    return database


def dump(database: Path) -> list[str]:
    with sqlite3.connect(database) as connection:
        return sorted(connection.iterdump())


def columns(connection: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in connection.execute(f"PRAGMA table_info({table})")]


def test_repeat_startup_preserves_every_record_schema_and_sequence(
    tmp_path: Path,
) -> None:
    database = populated(tmp_path)
    before = dump(database)
    records = fingerprint(database)
    assert set(records["tables"]) == {  # type: ignore[call-overload]
        table.name for table in TABLES
    }
    with fixture() as core, TestClient(create_app(database, platform=core.platform)):
        pass
    assert dump(database) == before
    assert fingerprint(database) == records


def test_additive_migration_keeps_the_records_this_version_reads(
    tmp_path: Path,
) -> None:
    database = populated(tmp_path)
    before = fingerprint(database)
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            ALTER TABLE drinks ADD COLUMN caffeine_mg INTEGER;
            ALTER TABLE study_sessions
                ADD COLUMN focus TEXT NOT NULL DEFAULT 'normal';
            CREATE TABLE study_tags (
                session_id TEXT NOT NULL REFERENCES study_sessions(id),
                tag TEXT NOT NULL
            );
            INSERT INTO study_tags SELECT id, 'exam' FROM study_sessions;
            CREATE INDEX drinks_recorded ON drinks(recorded_at);
            ANALYZE;
            """
        )
    assert fingerprint(database) == before


def test_live_column_order_reads_the_same_records(tmp_path: Path) -> None:
    database = populated(tmp_path)
    before = fingerprint(database)
    reordered = []
    with sqlite3.connect(database) as connection:
        for table, live in LIVE_TABLES.items():
            fresh = columns(connection, table)
            names = ", ".join(fresh)
            connection.execute(f"ALTER TABLE {table} RENAME TO fresh_{table}")
            connection.execute(live)
            connection.execute(
                f"INSERT INTO {table}({names}) "
                f"SELECT {names} FROM fresh_{table} ORDER BY rowid DESC"
            )
            connection.execute(f"DROP TABLE fresh_{table}")
            assert sorted(columns(connection, table)) == sorted(fresh)
            if columns(connection, table) != fresh:
                reordered.append(table)
    assert reordered == ["drinks"]
    assert fingerprint(database) == before


def test_a_later_release_may_read_new_nullable_columns_and_new_tables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = populated(tmp_path)
    before = fingerprint(database)
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            ALTER TABLE drinks ADD COLUMN caffeine_mg TEXT;
            CREATE TABLE study_tags (session_id TEXT PRIMARY KEY, tag TEXT NOT NULL);
            """
        )
    later = tuple(
        table._replace(columns=(*table.columns, "caffeine_mg"))
        if table.name == "drinks"
        else table
        for table in TABLES
    )
    monkeypatch.setattr(
        state_probe, "TABLES", (*later, Table("study_tags", ("session_id",), ("tag",)))
    )
    assert fingerprint(database) == before


@pytest.mark.parametrize(
    "change",
    [
        "UPDATE user_settings SET daily_goal_ml = 2601",
        "UPDATE drinks SET sweetness = NULL WHERE drink_type = 'coffee'",
        "UPDATE users SET display_name = CAST(display_name AS BLOB)",
        "DELETE FROM budget_transactions WHERE kind = 'daily_spend'",
        "DELETE FROM savings_goal_changes",
        "INSERT INTO users VALUES ('home-platform:kai', 'Kai', 'now')",
    ],
)
def test_changed_records_change_the_output(tmp_path: Path, change: str) -> None:
    database = populated(tmp_path)
    before = fingerprint(database)
    with sqlite3.connect(database) as connection:
        connection.execute(change)
    assert fingerprint(database) != before


@pytest.mark.parametrize(
    ("change", "missing"),
    [
        ("ALTER TABLE budget_transactions DROP COLUMN description", "description"),
        ("ALTER TABLE study_sessions RENAME COLUMN activity TO subject", "activity"),
        ("DROP TABLE study_sessions", "study_sessions"),
    ],
)
def test_destructive_schema_changes_fail(
    tmp_path: Path, change: str, missing: str
) -> None:
    database = populated(tmp_path)
    with sqlite3.connect(database) as connection:
        connection.execute(change)
    with pytest.raises(ValueError, match=missing):
        fingerprint(database)


def test_orphaned_records_fail(tmp_path: Path) -> None:
    database = populated(tmp_path)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO user_settings VALUES ('home-platform:nobody', 2000)"
        )
    with pytest.raises(ValueError, match="foreign-key"):
        fingerprint(database)


def test_probe_is_read_only_and_missing_file_is_not_created(tmp_path: Path) -> None:
    missing = tmp_path / "missing.db"
    with pytest.raises(sqlite3.OperationalError):
        fingerprint(missing)
    assert not missing.exists()
