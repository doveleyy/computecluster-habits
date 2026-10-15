from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from services.habit_tracker.budget import BudgetRepository, BudgetTransaction
from services.habit_tracker.main import create_app
from services.habit_tracker.state_probe import fingerprint
from services.habit_tracker.stats import (
    FocusInput,
    PeriodKind,
    SpendingInput,
    StatsInput,
    StatsRead,
    WaterInput,
    build_stats,
)
from services.habit_tracker.study import StudySession
from services.habit_tracker.tests.locks import answered_within, writer_holding
from services.habit_tracker.tests.members import client as member
from services.habit_tracker.water import Drink, WaterRepository
from tests.platform_fixture import fixture

SGT = ZoneInfo("Asia/Singapore")
METRICS = ("water", "spending", "focus")
PERIODS = ("week", "month", "year")
ALEX = {"X-Platform-User-Context": "member@example.test"}
SAM = {"X-Platform-User-Context": "someone-else@example.test"}


def at(day: date, hour: int = 12, timezone: ZoneInfo = SGT) -> datetime:
    return datetime.combine(day, time(hour), timezone).astimezone(UTC)


def drink(day: date, ml: int, hour: int = 12, kind: str = "water") -> Drink:
    moment = at(day, hour)
    return Drink(f"{day}-{hour}-{ml}", ml, kind, "normal", None, moment, moment)


def session(start: datetime, seconds: int, activity: str = "Reading") -> StudySession:
    end = start + timedelta(seconds=seconds)
    return StudySession(str(start), activity, 180, start, end, end, "stopped")


def stats(
    source: StatsInput,
    period: PeriodKind,
    anchor: date,
    now: datetime,
    timezone: ZoneInfo = SGT,
) -> StatsRead:
    return build_stats(
        source=source,
        period=period,
        anchor=anchor,
        now=now,
        timezone=timezone,
        currency="SGD",
    )


def test_bucket_counts_and_boundaries_in_a_leap_year() -> None:
    now = at(date(2028, 3, 10))
    empty = WaterInput(2000, [])
    february = stats(empty, "month", date(2028, 2, 15), now)
    assert (february.current.start, february.current.end) == (
        date(2028, 2, 1),
        date(2028, 2, 29),
    )
    assert len(february.current.buckets) == 29
    assert february.current.label == "February 2028"
    assert (february.previous.start, february.previous.end) == (
        date(2028, 1, 1),
        date(2028, 1, 31),
    )
    assert len(february.previous.buckets) == 31
    assert [b.label for b in february.current.buckets[:2]] == ["1", "2"]
    week = stats(empty, "week", date(2028, 3, 10), now)
    assert (week.current.start, week.current.end) == (
        date(2028, 3, 6),
        date(2028, 3, 12),
    )
    assert [b.label for b in week.current.buckets] == [
        "Mon",
        "Tue",
        "Wed",
        "Thu",
        "Fri",
        "Sat",
        "Sun",
    ]
    assert date(2028, 2, 29) in [b.start for b in week.previous.buckets]
    year = stats(empty, "year", date(2028, 3, 10), now)
    assert [b.start for b in year.current.buckets] == [
        date(2028, month, 1) for month in range(1, 13)
    ]
    assert year.current.end == date(2028, 12, 31)
    assert year.previous.start == date(2027, 1, 1)
    assert len(year.heatmap.days) == 365
    assert year.heatmap.days[-1].date == date(2028, 3, 10)
    assert year.summary.elapsed_days == 31 + 29 + 10


def test_future_buckets_are_null() -> None:
    today = date(2028, 3, 10)
    now = at(today, 18)
    source = WaterInput(2000, [drink(today, 2500), drink(today, 900, hour=23)])
    week = stats(source, "week", today, now)
    friday, saturday, sunday = week.current.buckets[4:]
    assert (friday.value, friday.target, friday.met) == (2500, 2000, True)
    for future in (saturday, sunday):
        assert future.value is None and future.met is None
        assert future.target == 2000
    year = stats(source, "year", today, now)
    assert year.current.buckets[2].value == 2500
    assert all(b.value is None for b in year.current.buckets[3:])
    assert all(b.target is b.met is None for b in year.current.buckets)
    assert week.pace is not None
    assert week.pace.cumulative[4:] == [2500, None, None]
    assert week.pace.target_cumulative == [2000 * n for n in range(1, 8)]


def test_previous_period_compares_like_for_like() -> None:
    today = date(2026, 10, 16)
    monday = today - timedelta(days=today.weekday())
    drinks = [drink(monday - timedelta(days=7 - n), 1000) for n in range(7)]
    drinks += [drink(monday + timedelta(days=n), 500, kind="tea") for n in range(4)]
    drinks += [drink(today, 3000, hour=9), drink(today, 400, hour=23)]
    week = stats(WaterInput(2000, drinks), "week", today, at(today, 15))
    summary = week.summary
    assert (summary.total, summary.elapsed_days, summary.daily_average) == (
        5000,
        5,
        1000,
    )
    assert (summary.compared_days, summary.compared_total) == (4, 2000)
    assert summary.previous_total_same_elapsed == 4000
    assert summary.change_ratio == -0.5
    assert [b.value for b in week.previous.buckets] == [1000] * 7
    assert [(b.key, b.value) for b in week.breakdown] == [
        ("water", 3000),
        ("tea", 2000),
    ]
    assert summary.best is not None and summary.best.start == today
    year_drinks = [drink(date(2025, 10, 15), 700), drink(date(2025, 10, 16), 900)]
    year = stats(WaterInput(2000, year_drinks), "year", today, at(today))
    assert year.summary.compared_days == 288
    assert year.summary.previous_total_same_elapsed == 700
    assert year.summary.change_ratio == -1
    first = stats(WaterInput(2000, year_drinks), "week", monday, at(monday))
    assert first.summary.compared_days == 0
    assert first.summary.change_ratio is None


def test_streak_counts_from_yesterday_while_today_is_unmet() -> None:
    today = date(2026, 10, 15)
    now = at(today, 20)
    focused = [1, 2, 3, 5, 6, 7, 8, 9]
    sessions = [session(at(today - timedelta(days=n)), 600) for n in focused]
    streak = stats(FocusInput(sessions), "month", today, now).streak
    assert (streak.current, streak.best_in_window, streak.window_days) == (3, 5, 365)
    sessions.append(session(at(today, 8), 60))
    assert stats(FocusInput(sessions), "month", today, now).streak.current == 4
    goal = 2000
    drinks = [drink(today - timedelta(days=n), goal) for n in (1, 2)]
    drinks.append(drink(today - timedelta(days=3), goal - 1))
    water = stats(WaterInput(goal, drinks), "week", today, now).streak
    assert (water.current, water.best_in_window) == (2, 2)


def test_allowance_changes_and_days_before_the_plan(tmp_path: Path) -> None:
    water = WaterRepository(tmp_path / "habits.db", SGT)
    budget = BudgetRepository(tmp_path / "habits.db", SGT)
    water.initialize()
    budget.initialize()
    plan = date(2026, 9, 10)
    water.ensure_user("alex", "Alex")
    budget.ensure_user("alex", plan)
    budget.set_daily_budget("alex", date(2026, 9, 15), 2300)
    for day in (date(2026, 9, 8), date(2026, 9, 12), date(2026, 9, 16)):
        budget.add_transaction("alex", "daily_spend", 1500, "Lunch", "food", day)
    budget.add_transaction(
        "alex", "fund_contribution", 9000, "Fund", day=date(2026, 9, 16)
    )
    first, end = date(2025, 8, 1), date(2026, 10, 1)
    source = SpendingInput(
        budget.allowances_in_range("alex", first, end),
        budget.spending_in_range("alex", first, end),
    )
    month = stats(source, "month", date(2026, 9, 20), at(date(2026, 9, 20)))
    by_day = {b.start: b for b in month.current.buckets}
    assert (by_day[date(2026, 9, 8)].target, by_day[date(2026, 9, 8)].met) == (
        None,
        None,
    )
    assert (by_day[date(2026, 9, 12)].target, by_day[date(2026, 9, 12)].met) == (
        1000,
        False,
    )
    assert (by_day[date(2026, 9, 16)].target, by_day[date(2026, 9, 16)].met) == (
        2300,
        True,
    )
    assert by_day[date(2026, 9, 25)].target == 2300
    assert month.summary.total == 4500
    assert (month.summary.days_with_target, month.summary.days_met) == (11, 10)
    assert month.summary.best == month.summary.best.__class__(
        start=date(2026, 9, 1), value=0
    )
    assert month.pace is not None
    assert month.pace.target_cumulative[8] is None
    assert month.pace.target_cumulative[9] == 1000
    assert month.pace.target_cumulative[14] == 5 * 1000 + 2300
    assert month.unit == "cents" and month.breakdown[0].key == "food"


def test_focus_splits_at_midnight_and_across_daylight_saving() -> None:
    today = date(2026, 10, 6)
    start = at(today - timedelta(days=1), 23) + timedelta(minutes=59, seconds=30)
    week = stats(FocusInput([session(start, 100)]), "week", today, at(today))
    assert [b.value for b in week.current.buckets[:2]] == [30, 70]
    assert (week.hours[23], week.hours[0]) == (30, 70)
    new_york = ZoneInfo("America/New_York")
    spring = datetime(2026, 3, 8, 6, 30, tzinfo=UTC)
    dst = stats(
        FocusInput([session(spring, 10800)]),
        "week",
        date(2026, 3, 8),
        spring + timedelta(hours=4),
        new_york,
    )
    assert dst.current.buckets[-1].value == 10800
    assert dst.hours[1:6] == [1800, 0, 3600, 3600, 1800]
    autumn = datetime(2026, 11, 1, 5, 30, tzinfo=UTC)
    fall = stats(
        FocusInput([session(autumn, 3600)]),
        "week",
        date(2026, 11, 1),
        autumn + timedelta(hours=2),
        new_york,
    )
    assert fall.hours[1] == 3600 and sum(fall.hours) == 3600
    kolkata = ZoneInfo("Asia/Kolkata")
    half_hour = at(today, 10, kolkata) + timedelta(minutes=15)
    india = stats(
        FocusInput([session(half_hour, 5400)]), "week", today, at(today, 20), kolkata
    )
    assert india.hours[10:12] == [2700, 2700]


def test_hours_skip_backdated_and_future_rows() -> None:
    today = date(2026, 10, 15)
    now = at(today, 20)
    yesterday = today - timedelta(days=1)
    backdated = Drink("b", 300, "water", "normal", None, at(yesterday), now)
    legacy = Drink("l", 200, "water", "normal", None, at(yesterday, 8), None)
    future = Drink("f", 900, "water", "normal", None, now + timedelta(hours=1))
    week = stats(
        WaterInput(2000, [backdated, legacy, future, drink(today, 100, 9)]),
        "week",
        today,
        now,
    )
    assert week.summary.total == 600
    assert week.hours[12] == 0 and week.hours[8] == 200 and week.hours[9] == 100
    spend = BudgetTransaction(
        "s", "daily_spend", 800, "Lunch", at(yesterday), "food", recorded_at=now
    )
    spending = stats(SpendingInput({}, [spend]), "week", today, now)
    assert spending.summary.total == 800 and sum(spending.hours) == 0


def test_route_validates_and_isolates_owners(tmp_path: Path) -> None:
    with (
        fixture() as core,
        TestClient(
            create_app(tmp_path / "habit-tracker.db", platform=core.platform)
        ) as browser,
    ):
        assert browser.get("/api/stats?metric=water&period=week").status_code == 401
        browser.post("/api/water/drinks", json={"amount_ml": 500}, headers=ALEX)
        browser.post(
            "/api/budget/transactions",
            json={"kind": "daily_spend", "amount_cents": 450, "category": "food"},
            headers=ALEX,
        )
        today = datetime.now(SGT).date()
        for query in (
            {"metric": "water", "period": "week", "anchor": str(today + timedelta(1))},
            {"metric": "steps", "period": "week"},
            {"metric": "water", "period": "decade"},
            {"metric": "water", "period": "week", "anchor": "invalid"},
            {"metric": "water", "period": "week", "anchor": "0001-01-01"},
            {
                "metric": "water",
                "period": "year",
                "anchor": str(today - timedelta(800)),
            },
            {"period": "week"},
        ):
            answer = browser.get("/api/stats", params=query, headers=ALEX)
            assert answer.status_code == 422, query
        alex = browser.get("/api/stats?metric=water&period=week", headers=ALEX).json()
        sam = browser.get("/api/stats?metric=water&period=week", headers=SAM).json()
        assert alex["today"] == str(today) and alex["summary"]["total"] == 500
        assert sam["summary"]["total"] == 0 and sam["breakdown"] == []
        alex = browser.get(
            "/api/stats?metric=spending&period=month", headers=ALEX
        ).json()
        sam = browser.get("/api/stats?metric=spending&period=month", headers=SAM)
        assert alex["breakdown"] == [{"key": "food", "value": 450}]
        assert sam.json()["summary"]["total"] == 0


def test_stats_answer_while_another_writer_holds_the_lock(tmp_path: Path) -> None:
    database = tmp_path / "habits.db"
    with member(database) as client:
        started = client.post(
            "/api/study/sessions", json={"activity": "Reading", "duration_minutes": 25}
        )
        assert started.status_code == 201
        with writer_holding(database):
            for metric in METRICS:
                answer = answered_within(
                    2, lambda m=metric: client.get(f"/api/stats?metric={m}&period=year")
                )
                assert answer.status_code == 200


@pytest.mark.parametrize("anchor_offset", [0, 40])
def test_every_metric_and_period_leaves_the_database_unchanged(
    tmp_path: Path, anchor_offset: int
) -> None:
    database = tmp_path / "habits.db"
    with member(database) as client:
        client.post("/api/water/drinks", json={"amount_ml": 250})
        client.post(
            "/api/budget/transactions",
            json={"kind": "daily_spend", "amount_cents": 700},
        )
        session = client.post(
            "/api/study/sessions", json={"activity": "Reading", "duration_minutes": 5}
        ).json()
        client.post(f"/api/study/sessions/{session['id']}/stop")
        before = fingerprint(database)
        anchor = datetime.now(SGT).date() - timedelta(days=anchor_offset)
        for metric in METRICS:
            for period in PERIODS:
                answer = client.get(
                    "/api/stats",
                    params={"metric": metric, "period": period, "anchor": str(anchor)},
                )
                assert answer.status_code == 200, (metric, period)
        assert fingerprint(database) == before
