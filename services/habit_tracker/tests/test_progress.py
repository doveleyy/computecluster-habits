from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from services.habit_tracker.budget import BudgetRepository, BudgetTransaction
from services.habit_tracker.main import create_app
from services.habit_tracker.progress import build_progress
from services.habit_tracker.study import StudyRepository, StudySession
from services.habit_tracker.tests.members import anonymous
from services.habit_tracker.tests.members import client as member
from services.habit_tracker.water import Drink, WaterRepository
from tests.platform_fixture import fixture


def test_authentication_week_boundaries_and_active_session(tmp_path: Path) -> None:
    with anonymous(tmp_path / "private.db") as client:
        assert client.get("/progress").status_code == 401
        assert client.get("/api/progress").status_code == 401
    with (
        fixture() as core,
        TestClient(
            create_app(tmp_path / "unlinked.db", platform=core.platform),
            headers={"X-Platform-User-Context": "member@example.test"},
        ) as client,
    ):
        core.status = 403
        assert client.get("/progress").status_code == 403
        assert client.get("/api/progress").status_code == 403
    with member(tmp_path / "habits.db", "alice") as client:
        assert client.get("/progress").status_code == 200
        assert 'id="progress-link"' in client.get("/").text
        for week in ("0001-01-01", "2026-09-29", "9999-12-27", "invalid"):
            assert client.get("/api/progress", params={"week": week}).status_code == 422
        session = client.post(
            "/api/study/sessions", json={"activity": "Reading", "duration_minutes": 180}
        ).json()
        snapshot = client.get("/api/progress").json()
        assert len(snapshot["days"]) == 14
        assert snapshot["week_start"] == snapshot["days"][7]["date"]
        assert client.get("/api/study/summary").json()["active"]["id"] == session["id"]
        assert snapshot["selected_week_records"]["sessions"] == []
        for day in snapshot["days"]:
            if day["temporal"] == "future":
                assert day["water"] is day["spending"] is day["focus"] is None


def test_owner_allowances_and_fund_exclusion(tmp_path: Path) -> None:
    path = tmp_path / "habits.db"
    tz = ZoneInfo("Asia/Singapore")
    water, budget = WaterRepository(path, tz), BudgetRepository(path, tz)
    water.initialize()
    budget.initialize()
    first = date(2026, 9, 28)
    for owner in ("alice", "bob"):
        water.ensure_user(owner, owner)
        budget.ensure_user(owner, first + timedelta(days=2))
        water.add_drink(owner, 250, "coffee", "hot", "none", first + timedelta(days=4))
        budget.add_transaction(
            owner, "daily_spend", 500, "Lunch", "food", first + timedelta(days=4)
        )
    budget.add_transaction(
        "alice", "fund_contribution", 10000, "Fund", day=first + timedelta(days=4)
    )
    budget.set_daily_budget("alice", first + timedelta(days=3), 0)
    budget.set_daily_budget("alice", first + timedelta(days=5), 2300)
    allowances = budget.allowances_in_range("alice", first, first + timedelta(days=14))
    assert allowances[first] is None
    assert allowances[first + timedelta(days=2)] == 1000
    assert allowances[first + timedelta(days=3)] == 0
    assert allowances[first + timedelta(days=5)] == 2300
    assert len(water.drinks_in_range("alice", first, first + timedelta(days=14))) == 1
    purchases = budget.spending_in_range("alice", first, first + timedelta(days=14))
    assert len(purchases) == 1 and purchases[0].amount_cents == 500
    assert purchases[0].recorded_at is not None
    assert purchases[0].recorded_at != purchases[0].occurred_at
    assert water.drinks_in_range("alice", first, first + timedelta(days=1)) == []


def test_midnight_split_totals_future_and_monday() -> None:
    tz = ZoneInfo("Asia/Singapore")
    week = date(2026, 10, 5)
    now = datetime(2026, 10, 8, 4, tzinfo=UTC)
    start = datetime(2026, 10, 5, 15, 59, 30, tzinfo=UTC)
    session = StudySession(
        "saved",
        "<Reading>",
        5,
        start,
        start + timedelta(minutes=5),
        start + timedelta(seconds=100),
        "stopped",
    )
    drinks = [
        Drink("water", 250, "water", "normal", None, now),
        Drink("coffee", 200, "coffee", "hot", "none", now),
        Drink("future", 500, "water", "normal", None, now + timedelta(hours=1)),
    ]
    purchases = [
        BudgetTransaction("spend", "daily_spend", 700, "<Lunch>", now, "food"),
        BudgetTransaction("fund", "fund_contribution", 10000, "Fund", now),
    ]
    result = build_progress(
        week_start=week,
        now=now,
        timezone=tz,
        current_goal_ml=2100,
        currency="SGD",
        drinks=drinks,
        spending=purchases,
        allowances={week: 0},
        sessions=[session],
    )
    assert result.completed_days == 3
    monday, tuesday, today = result.days[7], result.days[8], result.days[10]
    assert monday.focus is not None and monday.focus.total_seconds == 30
    assert tuesday.focus is not None and tuesday.focus.total_seconds == 70
    assert len(result.selected_week_records.sessions) == 1
    assert len(result.selected_week_records.drinks) == 2
    assert today.water is not None and today.spending is not None
    assert today.water.total_ml == 450 and today.water.plain_water_ml == 250
    assert today.water.by_temperature_ml == {"normal": 250, "hot": 200}
    assert today.spending.spent_cents == 700
    assert len(result.selected_week_records.purchases) == 1
    assert result.days[11].water is None
    assert result.days[11].spending is None
    assert result.days[11].focus is None
    assert monday.spending is not None and monday.spending.allowance_cents == 0
    assert today.spending.allowance_cents is None
    empty = build_progress(
        week_start=week,
        now=start,
        timezone=tz,
        current_goal_ml=2000,
        currency="SGD",
        drinks=[],
        spending=[],
        allowances={},
        sessions=[],
    )
    assert empty.completed_days == 0


def test_dst_overlap_and_legacy_long_session(tmp_path: Path) -> None:
    path = tmp_path / "habits.db"
    tz = ZoneInfo("America/New_York")
    water = WaterRepository(path, tz)
    water.initialize()
    water.ensure_user("alice", "Alice")
    study = StudyRepository(path, tz)
    study.initialize()
    start = datetime(2026, 3, 8, 6, 30, tzinfo=UTC)
    session = study.start("alice", "DST", 180, start)
    now = start + timedelta(hours=4)
    rows = study.saved_sessions_in_range(
        "alice", date(2026, 3, 8), date(2026, 3, 9), now
    )
    assert rows[0].id == session.id and rows[0].elapsed_seconds == 10800
    result = build_progress(
        week_start=date(2026, 3, 2),
        now=now,
        timezone=tz,
        current_goal_ml=2000,
        currency="SGD",
        drinks=[],
        spending=[],
        allowances={},
        sessions=rows,
    )
    assert result.days[-1].focus is not None
    assert result.days[-1].focus.total_seconds == 10800
    with study._connect() as connection:
        connection.execute(
            "UPDATE study_sessions SET started_at = ? WHERE id = ?",
            ((start - timedelta(days=2)).isoformat(), session.id),
        )
    assert (
        len(
            study.saved_sessions_in_range(
                "alice", date(2026, 3, 8), date(2026, 3, 9), now
            )
        )
        == 1
    )
    assert (
        study.saved_sessions_in_range("bob", date(2026, 3, 8), date(2026, 3, 9), now)
        == []
    )
