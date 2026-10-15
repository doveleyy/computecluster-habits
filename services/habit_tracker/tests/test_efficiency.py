"""Per-request cost: reads never take the write lock, reuse connections and
read only the records the response shows, however long the history."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from services.habit_tracker.budget import BudgetRepository
from services.habit_tracker.study import StudyRepository
from services.habit_tracker.tests.locks import answered_within, writer_holding
from services.habit_tracker.tests.members import client, owner
from services.habit_tracker.water import WaterRepository

TIMEZONE = ZoneInfo("Asia/Singapore")
LONG = "three-years@example.test"
SHORT = "four-months@example.test"


def hot_reads(today: date) -> dict[str, int]:
    """Each Overview, Water, Budget, Study and Progress read and its most
    SQL statements, identity included."""
    day = today.isoformat()
    return {
        "/api/water/today": 4,
        f"/api/water/day?date={day}": 4,
        f"/api/water/history?days=31&end={day}": 4,
        "/api/budget/summary": 12,
        "/api/budget/ledger?limit=5": 7,
        f"/api/budget/day?date={day}": 7,
        f"/api/budget/history?days=7&end={day}": 5,
        "/api/study/summary": 6,
        f"/api/study/history?days=31&end={day}": 4,
        "/api/progress": 11,
    }


@dataclass
class Work:
    connections: int = 0
    statements: list[str] = field(default_factory=list)
    rows: int = 0
    steps: int = 0

    def step(self) -> int:
        self.steps += 1
        return 0


WORK = Work()


class CountingCursor(sqlite3.Cursor):
    def __next__(self) -> Any:
        row = super().__next__()
        WORK.rows += 1
        return row

    def fetchone(self) -> Any:
        row = super().fetchone()
        WORK.rows += row is not None
        return row

    def fetchall(self) -> list[Any]:
        rows = super().fetchall()
        WORK.rows += len(rows)
        return rows


class CountingConnection(sqlite3.Connection):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        WORK.connections += 1
        self.set_trace_callback(WORK.statements.append)
        self.set_progress_handler(WORK.step, 1)

    def execute(self, sql: str, parameters: Any = (), /) -> sqlite3.Cursor:
        return self.cursor(CountingCursor).execute(sql, parameters)


@contextmanager
def counted(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    original = sqlite3.connect
    monkeypatch.setattr(
        sqlite3,
        "connect",
        lambda *args, **kwargs: original(*args, factory=CountingConnection, **kwargs),
    )
    yield
    monkeypatch.setattr(sqlite3, "connect", original)


def member_history(database: Path, user: str, days: int, today: date) -> None:
    """Every day alike: 8 drinks, 3 purchases and one focus session."""
    for repository in (WaterRepository, BudgetRepository, StudyRepository):
        repository(database, TIMEZONE).initialize()
    key = owner(user)
    first = today - timedelta(days=days - 1)
    WaterRepository(database, TIMEZONE).ensure_user(key, user)
    BudgetRepository(database, TIMEZONE).ensure_user(key, first)
    drinks, purchases, sessions = [], [], []
    for offset in range(days):
        day = first + timedelta(days=offset)

        def at(hour: int, day: date = day) -> str:
            return (
                datetime.combine(day, time(hour), TIMEZONE).astimezone(UTC).isoformat()
            )

        for hour in range(7, 23, 2):
            drinks.append((f"{key}-{day}-{hour}", key, 250, at(hour), at(hour)))
        for hour, cents in ((9, 300), (13, 700), (19, 1200)):
            purchases.append((f"{key}-{day}-{hour}", key, cents, at(hour), at(hour)))
        start = datetime.combine(day, time(6), TIMEZONE).astimezone(UTC)
        end = (start + timedelta(minutes=25)).isoformat()
        sessions.append((f"{key}-{day}", key, start.isoformat(), end, end))
    with sqlite3.connect(database) as connection:
        connection.executemany(
            "INSERT INTO drinks(id, owner_identity, amount_ml, consumed_at, "
            "recorded_at) VALUES (?, ?, ?, ?, ?)",
            drinks,
        )
        connection.executemany(
            "INSERT INTO budget_transactions(id, owner_identity, kind, amount_cents, "
            "description, occurred_at, category, recorded_at) "
            "VALUES (?, ?, 'daily_spend', ?, 'Fictional', ?, 'food', ?)",
            purchases,
        )
        connection.executemany(
            "INSERT INTO study_sessions VALUES "
            "(?, ?, 'Reading', 25, ?, ?, ?, 'completed')",
            sessions,
        )


def test_study_reads_answer_while_another_writer_holds_the_lock(
    tmp_path: Path,
) -> None:
    database = tmp_path / "habits.db"
    # Every request resolves the member through the core and ensures their rows.
    with client(database) as member:
        started = member.post(
            "/api/study/sessions", json={"activity": "Reading", "duration_minutes": 25}
        )
        assert started.status_code == 201
        with writer_holding(database):
            summary = answered_within(2, lambda: member.get("/api/study/summary"))
            history = answered_within(
                2, lambda: member.get("/api/study/history?days=7")
            )
    assert summary.status_code == 200
    assert summary.json()["active"]["activity"] == "Reading"
    assert history.status_code == 200
    assert len(history.json()["days"]) == 7


def test_hot_reads_cost_the_same_after_three_years_as_after_four_months(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "habits.db"
    today = datetime.now(TIMEZONE).date()
    member_history(database, LONG, 3 * 365, today)
    member_history(database, SHORT, 120, today)
    reads = hot_reads(today)
    rows: dict[str, dict[str, int]] = {}
    steps: dict[str, dict[str, int]] = {}
    with counted(monkeypatch):
        for user in (LONG, SHORT):
            with client(database, user=user) as member:
                for route in reads:
                    assert member.get(route).status_code == 200, route
                watcher = sqlite3.connect(database)
                version = watcher.execute("PRAGMA data_version").fetchone()
                rows[user], steps[user] = {}, {}
                with writer_holding(database):
                    WORK.connections = 0
                    for route, statements in reads.items():
                        WORK.statements.clear()
                        WORK.rows = WORK.steps = 0
                        response = answered_within(
                            2, lambda route=route: member.get(route)
                        )
                        assert response.status_code == 200, route
                        assert len(WORK.statements) <= statements, (
                            route,
                            WORK.statements,
                        )
                        rows[user][route], steps[user][route] = WORK.rows, WORK.steps
                    assert WORK.connections <= 1, "reads reuse a thread's connection"
                assert watcher.execute("PRAGMA data_version").fetchone() == version
                watcher.close()
    assert rows[LONG] == rows[SHORT]
    # The fund balance and movements and the five latest sessions still rank
    # or sum every row inside SQLite; all other reads touch only their days.
    whole_history = {
        "/api/budget/summary",
        "/api/budget/ledger?limit=5",
        "/api/study/summary",
    }
    for route in reads.keys() - whole_history:
        assert steps[LONG][route] <= steps[SHORT][route] * 1.1 + 50, (
            route,
            steps[LONG][route],
            steps[SHORT][route],
        )
