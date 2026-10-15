from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from services.habit_tracker.budget import BudgetTransaction
from services.habit_tracker.study import StudySession
from services.habit_tracker.water import Drink


@dataclass(frozen=True)
class WaterDay:
    total_ml: int
    plain_water_ml: int
    entry_count: int
    by_type_ml: dict[str, int]
    by_temperature_ml: dict[str, int]


@dataclass(frozen=True)
class SpendingDay:
    spent_cents: int
    allowance_cents: int | None
    entry_count: int
    by_category_cents: dict[str, int]


@dataclass(frozen=True)
class FocusDay:
    total_seconds: int
    session_count: int
    by_activity_seconds: dict[str, int]


@dataclass(frozen=True)
class ProgressDay:
    date: date
    temporal: Literal["past", "today", "future"]
    water: WaterDay | None
    spending: SpendingDay | None
    focus: FocusDay | None


@dataclass(frozen=True)
class DaySegment:
    date: date
    seconds: int


@dataclass(frozen=True)
class SavedSession:
    id: str
    started_at: datetime
    ended_at: datetime
    activity: str
    status: str
    day_segments: list[DaySegment]


@dataclass(frozen=True)
class WeekRecords:
    drinks: list[Drink]
    purchases: list[BudgetTransaction]
    sessions: list[SavedSession]


@dataclass(frozen=True)
class ProgressRead:
    week_start: date
    today: date
    timezone: str
    generated_at: datetime
    current_goal_ml: int
    currency: str
    completed_days: int
    days: list[ProgressDay]
    selected_week_records: WeekRecords


def validate_week(week: date, today: date) -> None:
    if week.weekday() != 0 or week > today:
        raise ValueError("Choose a Monday no later than the current week.")
    try:
        week - timedelta(days=8)
        week + timedelta(days=8)
    except OverflowError as error:
        raise ValueError("Week is outside the supported date range.") from error


def day_segments(
    session: StudySession, timezone: ZoneInfo, now: datetime
) -> list[DaySegment]:
    """Split a saved session at each local midnight, ignoring time after now."""
    if session.ended_at is None:
        return []
    end = min(session.ended_at, now)
    start = session.started_at
    day = start.astimezone(timezone).date()
    segments: list[DaySegment] = []
    while start < end:
        midnight = datetime.combine(
            day + timedelta(days=1), time.min, timezone
        ).astimezone(UTC)
        seconds = int((min(end, midnight) - start).total_seconds())
        if seconds:
            segments.append(DaySegment(day, seconds))
        start, day = midnight, day + timedelta(days=1)
    return segments


def build_progress(
    *,
    week_start: date,
    now: datetime,
    timezone: ZoneInfo,
    current_goal_ml: int,
    currency: str,
    drinks: list[Drink],
    spending: list[BudgetTransaction],
    allowances: dict[date, int | None],
    sessions: list[StudySession],
) -> ProgressRead:
    today = now.astimezone(timezone).date()
    validate_week(week_start, today)
    first = week_start - timedelta(days=7)
    end = week_start + timedelta(days=7)
    days: list[ProgressDay] = []
    segments: dict[str, list[DaySegment]] = {}
    focus: dict[date, list[tuple[StudySession, int]]] = {}
    for session in sessions:
        for segment in day_segments(session, timezone, now):
            focus.setdefault(segment.date, []).append((session, segment.seconds))
    for offset in range(14):
        day = first + timedelta(days=offset)
        if day > today:
            days.append(ProgressDay(day, "future", None, None, None))
            continue
        day_drinks = [
            d
            for d in drinks
            if d.consumed_at.astimezone(timezone).date() == day and d.consumed_at <= now
        ]
        day_spending = [
            s
            for s in spending
            if s.kind == "daily_spend"
            and s.occurred_at.astimezone(timezone).date() == day
            and s.occurred_at <= now
        ]
        types: dict[str, int] = {}
        temperatures: dict[str, int] = {}
        categories: dict[str, int] = {}
        activities: dict[str, int] = {}
        for drink in day_drinks:
            types[drink.drink_type] = types.get(drink.drink_type, 0) + drink.amount_ml
            temperatures[drink.temperature] = (
                temperatures.get(drink.temperature, 0) + drink.amount_ml
            )
        for purchase in day_spending:
            categories[purchase.category] = (
                categories.get(purchase.category, 0) + purchase.amount_cents
            )
        count = 0
        for session, seconds in focus.get(day, []):
            count += 1
            activities[session.activity] = activities.get(session.activity, 0) + seconds
            if day >= week_start:
                segments.setdefault(session.id, []).append(DaySegment(day, seconds))
        days.append(
            ProgressDay(
                day,
                "today" if day == today else "past",
                WaterDay(
                    sum(types.values()),
                    types.get("water", 0),
                    len(day_drinks),
                    types,
                    temperatures,
                ),
                SpendingDay(
                    sum(categories.values()),
                    allowances.get(day),
                    len(day_spending),
                    categories,
                ),
                FocusDay(sum(activities.values()), count, activities),
            )
        )
    records = WeekRecords(
        [
            d
            for d in drinks
            if week_start <= d.consumed_at.astimezone(timezone).date() < end
            and d.consumed_at <= now
        ],
        [
            s
            for s in spending
            if s.kind == "daily_spend"
            and week_start <= s.occurred_at.astimezone(timezone).date() < end
            and s.occurred_at <= now
        ],
        [
            SavedSession(
                s.id, s.started_at, s.ended_at, s.activity, s.status, segments[s.id]
            )
            for s in sessions
            if s.id in segments and s.ended_at is not None
        ],
    )
    return ProgressRead(
        week_start,
        today,
        str(timezone),
        now,
        current_goal_ml,
        currency,
        min(7, (today - week_start).days),
        days,
        records,
    )
