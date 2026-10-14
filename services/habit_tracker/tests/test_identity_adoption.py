"""Offline adoption moves legacy rows to a stable owner: every table, all or none.

The gateway carries no Tailscale login, so adoption is no longer triggered by a
request. An operator-reviewed mapping runs the repositories' adoption steps in
one shared transaction, and the member then sees the data through the core.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from services.habit_tracker.budget import BudgetRepository
from services.habit_tracker.study import StudyRepository
from services.habit_tracker.tests.members import client, owner
from services.habit_tracker.water import WaterRepository

TIMEZONE = ZoneInfo("Asia/Singapore")
LEGACY = "tailscale:member@example.test"
LINKED = owner("member@example.test")
OWNER_TABLES = (
    ("users", "identity"),
    ("user_settings", "identity"),
    ("drinks", "owner_identity"),
    ("budget_settings", "identity"),
    ("daily_budget_changes", "identity"),
    ("budget_transactions", "owner_identity"),
    ("budget_adjustments", "owner_identity"),
    ("study_sessions", "owner_identity"),
)


def owners(database: Path) -> dict[str, set[str]]:
    with sqlite3.connect(database) as connection:
        return {
            table: {
                str(row[0])
                for row in connection.execute(
                    f"SELECT DISTINCT {column} FROM {table}"
                ).fetchall()
            }
            for table, column in OWNER_TABLES
        }


def repositories(
    database: Path,
) -> tuple[WaterRepository, BudgetRepository, StudyRepository]:
    return (
        WaterRepository(database, TIMEZONE),
        BudgetRepository(database, TIMEZONE),
        StudyRepository(database, TIMEZONE),
    )


def legacy_era(database: Path) -> None:
    water, budget, study = repositories(database)
    for repository in (water, budget, study):
        repository.initialize()
    today = datetime.now(TIMEZONE).date()
    water.ensure_user(LEGACY, "member")
    budget.ensure_user(LEGACY, today)
    water.set_goal(LEGACY, 3000)
    budget.set_daily_budget(LEGACY, today, 2500)
    water.add_drink(LEGACY, 250, "water", "normal", None)
    budget.add_transaction(LEGACY, "daily_spend", 500, "Daily spending")
    session = study.start(LEGACY, "Reading", 25)
    study.finish(LEGACY, session.id, "stop")
    assert owners(database) == {table: {LEGACY} for table, _ in OWNER_TABLES}


def adopt(database: Path, study: StudyRepository | None = None) -> None:
    water, budget, default_study = repositories(database)
    study = study or default_study
    with water.transaction() as connection:
        water.adopt_identity(connection, LEGACY, LINKED, "member@example.test")
        budget.adopt_identity(connection, LEGACY, LINKED, datetime.now(TIMEZONE).date())
        study.adopt_identity(connection, LEGACY, LINKED)


def test_every_table_moves_to_the_linked_identity(tmp_path: Path) -> None:
    database = tmp_path / "habits.db"
    legacy_era(database)
    adopt(database)
    adopt(database)
    assert owners(database) == {table: {LINKED} for table, _ in OWNER_TABLES}
    with client(database) as member:
        today = member.get("/api/water/today").json()
        study = member.get("/api/study/summary").json()
        goal = member.get("/api/water/settings").json()
        summary = member.get("/api/budget/summary").json()
    assert today["total_ml"] == 250
    assert [session["activity"] for session in study["recent"]] == ["Reading"]
    assert goal["daily_goal_ml"] == 3000
    assert summary["daily_budget_cents"] == 2500
    assert summary["daily_spent_cents"] == 500


def test_a_partial_adoption_failure_moves_nothing_and_retry_completes(
    tmp_path: Path,
) -> None:
    database = tmp_path / "habits.db"
    legacy_era(database)
    before = owners(database)
    failing = StudyRepository(database, TIMEZONE)

    def locked(*_: object) -> None:
        raise sqlite3.OperationalError("database is locked")

    failing.adopt_identity = locked  # type: ignore[method-assign]
    with pytest.raises(sqlite3.OperationalError):
        adopt(database, failing)
    assert owners(database) == before, "nothing moves until every table moves"
    adopt(database)
    assert owners(database) == {table: {LINKED} for table, _ in OWNER_TABLES}
    with client(database) as member:
        assert member.get("/api/water/settings").json()["daily_goal_ml"] == 3000
        assert member.get("/api/budget/summary").json()["daily_budget_cents"] == 2500
