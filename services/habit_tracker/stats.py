"""Read-only statistics for one metric over a week, month or year.

The builder is pure: the route reads one date range per table and passes the
rows in. Days are local days in the household timezone, values are integers
in the metric's unit, and days after today have no value.
"""

from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict

from services.habit_tracker.budget import BudgetTransaction
from services.habit_tracker.progress import day_segments
from services.habit_tracker.study import StudySession
from services.habit_tracker.water import Drink

Metric = Literal["water", "spending", "focus"]
PeriodKind = Literal["week", "month", "year"]

HEATMAP_DAYS = 365
# A current-year view compares against the whole previous year, so the widest
# accepted read is two calendar years, one of them leap.
MAX_RANGE_DAYS = 731
MONTHS = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)
MONTH_NAMES = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
UNITS: dict[Metric, str] = {"water": "ml", "spending": "cents", "focus": "seconds"}


class _Read(BaseModel):
    model_config = ConfigDict(frozen=True)


class Bucket(_Read):
    start: date
    label: str
    value: int | None
    target: int | None
    met: bool | None


class PeriodRead(_Read):
    start: date
    end: date
    label: str
    buckets: list[Bucket]


class Best(_Read):
    start: date
    value: int


class Summary(_Read):
    total: int
    daily_average: float
    elapsed_days: int
    compared_days: int
    compared_total: int
    previous_total_same_elapsed: int
    change_ratio: float | None
    days_met: int
    days_with_target: int
    best: Best | None


class Streak(_Read):
    current: int
    best_in_window: int
    window_days: int


class HeatDay(_Read):
    date: date
    value: int
    met: bool | None


class Heatmap(_Read):
    start: date
    days: list[HeatDay]


class BreakdownItem(_Read):
    key: str
    value: int


class Pace(_Read):
    cumulative: list[int | None]
    target_cumulative: list[int | None]


class StatsRead(_Read):
    metric: Metric
    period: PeriodKind
    unit: str
    currency: str
    timezone: str
    today: date
    generated_at: datetime
    current: PeriodRead
    previous: PeriodRead
    summary: Summary
    streak: Streak
    heatmap: Heatmap
    breakdown: list[BreakdownItem]
    hours: list[int]
    pace: Pace | None


@dataclass(frozen=True)
class WaterInput:
    """Drinks and the current goal.

    There is no goal history, so every day, past ones included, is judged
    against today's goal.
    """

    goal_ml: int
    drinks: list[Drink]


@dataclass(frozen=True)
class SpendingInput:
    """Purchases and each day's effective allowance (None before the plan)."""

    allowances: dict[date, int | None]
    purchases: list[BudgetTransaction]


@dataclass(frozen=True)
class FocusInput:
    sessions: list[StudySession]


StatsInput = WaterInput | SpendingInput | FocusInput


@dataclass(frozen=True)
class Span:
    kind: PeriodKind
    start: date
    end: date

    def days(self) -> list[date]:
        return [
            self.start + timedelta(days=offset)
            for offset in range((self.end - self.start).days + 1)
        ]


def _month_start_after(day: date) -> date:
    return (day.replace(day=28) + timedelta(days=4)).replace(day=1)


def period_span(kind: PeriodKind, anchor: date) -> Span:
    match kind:
        case "week":
            start = anchor - timedelta(days=anchor.weekday())
            return Span(kind, start, start + timedelta(days=6))
        case "month":
            start = anchor.replace(day=1)
            return Span(kind, start, _month_start_after(start) - timedelta(days=1))
        case "year":
            return Span(kind, date(anchor.year, 1, 1), date(anchor.year, 12, 31))


def previous_span(span: Span) -> Span:
    return period_span(span.kind, span.start - timedelta(days=1))


def fetch_range(kind: PeriodKind, anchor: date, today: date) -> tuple[date, date]:
    """Return the local days [first, end) covering heatmap, previous and current."""
    if anchor > today:
        raise ValueError("Choose a date no later than today.")
    try:
        current = period_span(kind, anchor)
        first = min(
            today - timedelta(days=HEATMAP_DAYS - 1), previous_span(current).start
        )
        end = max(current.end, today) + timedelta(days=1)
    except (OverflowError, ValueError) as error:
        raise ValueError("Date is outside the supported range.") from error
    if (end - first).days > MAX_RANGE_DAYS:
        raise ValueError(
            f"Choose a more recent period; one request reads at most "
            f"{MAX_RANGE_DAYS} days."
        )
    return first, end


@dataclass(frozen=True)
class _Series:
    metric: Metric
    items: list[tuple[date, str, int]]
    hourly: list[tuple[date, int, int]]
    target: Callable[[date], int | None]


def _local(moment: datetime, timezone: ZoneInfo) -> datetime:
    return moment.astimezone(timezone)


def _entries(
    rows: Iterable[tuple[datetime, datetime | None, str, int]],
    now: datetime,
    timezone: ZoneInfo,
) -> tuple[list[tuple[date, str, int]], list[tuple[date, int, int]]]:
    """Split timestamped rows into day items and same-day-recorded hour items.

    A backdated row sits at local noon of an earlier day, so its hour is not
    when it happened; rows without a recording time predate that rule.
    """
    items: list[tuple[date, str, int]] = []
    hourly: list[tuple[date, int, int]] = []
    for moment, recorded_at, key, amount in rows:
        if moment > now:
            continue
        local = _local(moment, timezone)
        items.append((local.date(), key, amount))
        if recorded_at is None or _local(recorded_at, timezone).date() == local.date():
            hourly.append((local.date(), local.hour, amount))
    return items, hourly


def _hour_pieces(
    session: StudySession, timezone: ZoneInfo, now: datetime
) -> Iterator[tuple[date, int, int]]:
    if session.ended_at is None:
        return
    end = min(session.ended_at, now)
    start = session.started_at
    while start < end:
        local = _local(start, timezone)
        boundary = start + timedelta(
            minutes=60 - local.minute,
            seconds=-local.second,
            microseconds=-local.microsecond,
        )
        piece_end = min(end, boundary)
        yield local.date(), local.hour, int((piece_end - start).total_seconds())
        start = piece_end


def _series(source: StatsInput, now: datetime, timezone: ZoneInfo) -> _Series:
    match source:
        case WaterInput(goal_ml=goal, drinks=drinks):
            items, hourly = _entries(
                (
                    (d.consumed_at, d.recorded_at, d.drink_type, d.amount_ml)
                    for d in drinks
                ),
                now,
                timezone,
            )
            return _Series("water", items, hourly, lambda _day: goal)
        case SpendingInput(allowances=allowances, purchases=purchases):
            items, hourly = _entries(
                (
                    (p.occurred_at, p.recorded_at, p.category, p.amount_cents)
                    for p in purchases
                    if p.kind == "daily_spend"
                ),
                now,
                timezone,
            )
            return _Series("spending", items, hourly, allowances.get)
        case FocusInput(sessions=sessions):
            return _Series(
                "focus",
                [
                    (segment.date, session.activity, segment.seconds)
                    for session in sessions
                    for segment in day_segments(session, timezone, now)
                ],
                [
                    piece
                    for session in sessions
                    for piece in _hour_pieces(session, timezone, now)
                ],
                lambda _day: None,
            )


def _met(metric: Metric, value: int, target: int | None) -> bool | None:
    if metric == "focus":
        return value > 0
    if target is None:
        return None
    return value >= target if metric == "water" else value <= target


def _bucket_days(span: Span) -> list[tuple[str, list[date]]]:
    if span.kind != "year":
        return [
            (WEEKDAYS[day.weekday()] if span.kind == "week" else str(day.day), [day])
            for day in span.days()
        ]
    return [
        (
            MONTHS[month - 1],
            period_span("month", date(span.start.year, month, 1)).days(),
        )
        for month in range(1, 13)
    ]


def _period_label(span: Span) -> str:
    match span.kind:
        case "week":
            start = span.start
            return f"Week of {start.day} {MONTHS[start.month - 1]} {start.year}"
        case "month":
            return f"{MONTH_NAMES[span.start.month - 1]} {span.start.year}"
        case "year":
            return str(span.start.year)


def build_stats(
    *,
    source: StatsInput,
    period: PeriodKind,
    anchor: date,
    now: datetime,
    timezone: ZoneInfo,
    currency: str,
) -> StatsRead:
    today = _local(now, timezone).date()
    fetch_range(period, anchor, today)
    current = period_span(period, anchor)
    previous = previous_span(current)
    series = _series(source, now, timezone)
    metric = series.metric
    totals: dict[date, int] = {}
    for day, _key, amount in series.items:
        totals[day] = totals.get(day, 0) + amount

    def judged(day: date) -> bool | None:
        return _met(metric, totals.get(day, 0), series.target(day))

    def read(span: Span) -> PeriodRead:
        buckets = []
        for label, days in _bucket_days(span):
            elapsed = [day for day in days if day <= today]
            single = days[0] if len(days) == 1 else None
            buckets.append(
                Bucket(
                    start=days[0],
                    label=label,
                    value=sum(totals.get(d, 0) for d in elapsed) if elapsed else None,
                    target=series.target(single) if single is not None else None,
                    met=judged(single) if single is not None and elapsed else None,
                )
            )
        return PeriodRead(
            start=span.start, end=span.end, label=_period_label(span), buckets=buckets
        )

    current_read = read(current)
    elapsed = [day for day in current.days() if day <= today]
    compared = [day for day in elapsed if day < today]
    total = sum(totals.get(day, 0) for day in elapsed)
    compared_total = sum(totals.get(day, 0) for day in compared)
    previous_total = sum(totals.get(day, 0) for day in previous.days()[: len(compared)])
    judgments = [judged(day) for day in elapsed]
    heat_start = today - timedelta(days=HEATMAP_DAYS - 1)
    heat = [
        HeatDay(date=day, value=totals.get(day, 0), met=judged(day))
        for day in (heat_start + timedelta(days=n) for n in range(HEATMAP_DAYS))
    ]
    shown = set(elapsed)
    breakdown: dict[str, int] = {}
    for day, key, amount in series.items:
        if day in shown:
            breakdown[key] = breakdown.get(key, 0) + amount
    hours = [0] * 24
    for day, hour, amount in series.hourly:
        if day in shown:
            hours[hour] += amount
    return StatsRead(
        metric=metric,
        period=period,
        unit=UNITS[metric],
        currency=currency,
        timezone=str(timezone),
        today=today,
        generated_at=now,
        current=current_read,
        previous=read(previous),
        summary=Summary(
            total=total,
            daily_average=total / len(elapsed),
            elapsed_days=len(elapsed),
            compared_days=len(compared),
            compared_total=compared_total,
            previous_total_same_elapsed=previous_total,
            change_ratio=(
                (compared_total - previous_total) / previous_total
                if previous_total
                else None
            ),
            days_met=sum(1 for judgment in judgments if judgment is True),
            days_with_target=sum(1 for judgment in judgments if judgment is not None),
            best=_best(metric, current_read.buckets, today, period),
        ),
        streak=_streak(heat),
        heatmap=Heatmap(start=heat_start, days=heat),
        breakdown=[
            BreakdownItem(key=key, value=value)
            for key, value in sorted(breakdown.items(), key=lambda kv: (-kv[1], kv[0]))
        ],
        hours=hours,
        pace=(
            _pace(current_read.buckets, today)
            if metric != "focus" and period != "year"
            else None
        ),
    )


def _best(
    metric: Metric, buckets: list[Bucket], today: date, period: PeriodKind
) -> Best | None:
    """Highest water or focus bucket; lowest spending bucket already complete.

    The bucket holding today is still open, so it cannot be the lowest spend.
    """
    if metric == "spending":
        current = period_span("month", today).start if period == "year" else today
        closed = [b for b in buckets if b.value is not None and b.start < current]
        if not closed:
            return None
        low = min(closed, key=lambda b: (b.value or 0, b.start))
        return Best(start=low.start, value=low.value or 0)
    shown = [b for b in buckets if b.value]
    if not shown:
        return None
    high = max(shown, key=lambda b: (b.value or 0, -b.start.toordinal()))
    return Best(start=high.start, value=high.value or 0)


def _streak(heat: list[HeatDay]) -> Streak:
    """Count met days ending today, or ending yesterday while today is unmet.

    An unmet today is still open, so it does not break the current streak.
    """
    days = heat if heat[-1].met else heat[:-1]
    current = 0
    for day in reversed(days):
        if not day.met:
            break
        current += 1
    best = run = 0
    for day in heat:
        run = run + 1 if day.met else 0
        best = max(best, run)
    return Streak(current=current, best_in_window=best, window_days=len(heat))


def _pace(buckets: list[Bucket], today: date) -> Pace:
    cumulative: list[int | None] = []
    target_cumulative: list[int | None] = []
    actual = 0
    planned: int | None = None
    for bucket in buckets:
        if bucket.value is not None:
            actual += bucket.value
        cumulative.append(actual if bucket.start <= today else None)
        if bucket.target is not None:
            planned = (planned or 0) + bucket.target
        target_cumulative.append(planned)
    return Pace(cumulative=cumulative, target_cumulative=target_cumulative)
