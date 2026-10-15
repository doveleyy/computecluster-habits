"""Budget reads that touch only the days they show still settle every day.

The reference below is the full-history algorithm that ``BudgetRepository``
used before its reads were bounded: walk every day from the plan start and
assign each purchase to its local day in Python. Randomized fictional
histories cover allowance changes, backdated and boundary entries, fund
movements, timezones with daylight saving and plans starting after the day
being read.
"""

from __future__ import annotations

import random
import sqlite3
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from services.habit_tracker.budget import BudgetDay, BudgetRepository, FundEntry
from services.habit_tracker.water import WaterRepository

OWNER = "home-platform:fictional-equivalence"
OTHER = "home-platform:fictional-neighbour"
TIMEZONES = (
    "Asia/Singapore",
    "America/Santiago",
    "America/Havana",
    "America/New_York",
    "Europe/London",
    "Australia/Lord_Howe",
    "Pacific/Chatham",
    "UTC",
)


def reference_days(
    connection: sqlite3.Connection, timezone: ZoneInfo, end_day: date
) -> list[BudgetDay]:
    (plan_start,) = connection.execute(
        "SELECT plan_start_date FROM budget_settings WHERE identity = ?", (OWNER,)
    ).fetchone()
    changes = connection.execute(
        "SELECT effective_date, amount_cents FROM daily_budget_changes "
        "WHERE identity = ? AND effective_date <= ? ORDER BY effective_date",
        (OWNER, end_day.isoformat()),
    ).fetchall()
    spent: dict[date, int] = {}
    for amount, occurred_at in connection.execute(
        "SELECT amount_cents, occurred_at FROM budget_transactions "
        "WHERE owner_identity = ? AND kind = 'daily_spend'",
        (OWNER,),
    ):
        day = datetime.fromisoformat(occurred_at).astimezone(timezone).date()
        spent[day] = spent.get(day, 0) + amount
    day = min(date.fromisoformat(plan_start), end_day)
    result = []
    while day <= end_day:
        budget = 0
        for change_day, amount in changes:
            if date.fromisoformat(change_day) <= day:
                budget = amount
        result.append(BudgetDay(day, budget, spent.get(day, 0)))
        day += timedelta(days=1)
    return result


def reference_ledger(
    connection: sqlite3.Connection, timezone: ZoneInfo, today: date, limit: int
) -> list[FundEntry]:
    entries = [
        FundEntry(
            key=f"settlement:{day.day.isoformat()}",
            entry_date=day.day,
            kind="daily_settlement",
            amount_cents=day.net_cents,
            description="Daily surplus" if day.net_cents >= 0 else "Daily overage",
        )
        for day in reference_days(connection, timezone, today)[:-1]
        if day.net_cents != 0
    ]
    for key, kind, amount, description, occurred, recorded in connection.execute(
        "SELECT id, kind, amount_cents, description, occurred_at, recorded_at "
        "FROM budget_transactions WHERE owner_identity = ? "
        "AND kind IN ('fund_redemption', 'fund_contribution')",
        (OWNER,),
    ):
        occurred_at = datetime.fromisoformat(occurred)
        entries.append(
            FundEntry(
                key=f"transaction:{key}",
                entry_date=occurred_at.astimezone(timezone).date(),
                kind=kind,
                amount_cents=amount if kind == "fund_contribution" else -amount,
                description=description,
                occurred_at=occurred_at,
                backdated=(
                    recorded is not None
                    and datetime.fromisoformat(recorded) != occurred_at
                ),
            )
        )
    for key, effective, previous, new, occurred in connection.execute(
        "SELECT id, effective_date, previous_amount_cents, new_amount_cents, "
        "occurred_at FROM budget_adjustments WHERE owner_identity = ?",
        (OWNER,),
    ):
        entries.append(
            FundEntry(
                key=f"adjustment:{key}",
                entry_date=date.fromisoformat(effective),
                kind="daily_budget_adjustment",
                amount_cents=new - previous,
                description="Daily budget changed",
                occurred_at=datetime.fromisoformat(occurred),
                previous_amount_cents=previous,
                new_amount_cents=new,
            )
        )
    entries.sort(
        key=lambda entry: (
            entry.entry_date,
            entry.occurred_at or datetime.min.replace(tzinfo=UTC),
            entry.key,
        ),
        reverse=True,
    )
    return entries[:limit]


def moment(rng: random.Random, day: date, timezone: ZoneInfo) -> datetime:
    midnight = datetime.combine(day, time.min, timezone)
    match rng.randrange(6):
        case 0:
            local = midnight
        case 1:
            local = datetime.combine(day, time(12), timezone)
        case 2:
            local = datetime.combine(day, time(23, 59, 59, 999_999), timezone)
        case 3:
            local = datetime.combine(day, time(23, 59, 59), timezone)
        case _:
            local = midnight + timedelta(
                seconds=rng.randrange(86_400),
                microseconds=rng.choice((0, rng.randrange(1_000_000))),
            )
    return local.astimezone(UTC)


def history(path: Path, rng: random.Random) -> tuple[BudgetRepository, date]:
    timezone = ZoneInfo(rng.choice(TIMEZONES))
    water = WaterRepository(path, timezone)
    budget = BudgetRepository(path, timezone)
    water.initialize()
    budget.initialize()
    today = date(2024, 1, 1) + timedelta(days=rng.randrange(900))
    plan_start = today - timedelta(days=rng.randrange(-3, 300))
    for owner in (OWNER, OTHER):
        water.ensure_user(owner, "Fictional member")
        budget.ensure_user(owner, plan_start)
    span = max(1, (today - plan_start).days + 40)
    for _ in range(rng.randrange(7)):
        budget.set_daily_budget(
            OWNER,
            plan_start + timedelta(days=rng.randrange(-30, span)),
            rng.choice((0, 500, 1000, 1234, 2500, 1_000_000)),
        )
    rows = []
    for _ in range(rng.randrange(400)):
        day = plan_start + timedelta(days=rng.randrange(-20, span))
        occurred = moment(rng, day, timezone)
        recorded = occurred + timedelta(days=rng.choice((0, 0, 0, 1, 5)))
        kind = rng.choice(
            ("daily_spend",) * 8 + ("fund_redemption", "fund_contribution")
        )
        owner = OWNER if rng.random() < 0.9 else OTHER
        rows.append(
            (
                str(uuid4()),
                owner,
                kind,
                rng.randrange(1, 5000),
                "Fictional",
                occurred.isoformat(),
                recorded.isoformat(),
            )
        )
    with sqlite3.connect(path) as connection:
        connection.executemany(
            "INSERT INTO budget_transactions(id, owner_identity, kind, "
            "amount_cents, description, occurred_at, recorded_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
    return budget, today


def test_bounded_budget_reads_match_the_full_history_settlement(
    tmp_path: Path,
) -> None:
    rng = random.Random(20261016)
    for trial in range(80):
        path = tmp_path / f"budget-{trial}.db"
        budget, today = history(path, rng)
        timezone = budget._timezone
        connection = sqlite3.connect(path)
        days = reference_days(connection, timezone, today)
        summary = budget.summary(OWNER, today)
        completed = sum(day.net_cents for day in days[:-1])
        movements = dict(
            connection.execute(
                "SELECT kind, SUM(amount_cents) FROM budget_transactions "
                "WHERE owner_identity = ? AND kind != 'daily_spend' GROUP BY kind",
                (OWNER,),
            ).fetchall()
        )
        settled = (
            completed
            + movements.get("fund_contribution", 0)
            - movements.get("fund_redemption", 0)
        )
        context = f"trial {trial} in {timezone} on {today}"
        assert (
            summary.daily_budget_cents,
            summary.daily_spent_cents,
            summary.settled_fund_cents,
            summary.fund_balance_cents,
            summary.pending_surplus_cents,
        ) == (
            days[-1].budget_cents,
            days[-1].spent_cents,
            settled,
            settled + min(days[-1].net_cents, 0),
            max(days[-1].net_cents, 0),
        ), context
        for limit in (1, 5, 50, 200):
            assert budget.fund_ledger(OWNER, today, limit) == reference_ledger(
                connection, timezone, today, limit
            ), f"{context}, ledger limit {limit}"
        for _ in range(4):
            end = today - timedelta(days=rng.randrange(-1, 330))
            count = rng.choice((1, 7, 31, 90))
            expected = reference_days(connection, timezone, end)
            assert budget.history(OWNER, end, count) == expected[-count:], (
                f"{context}, history {count} to {end}"
            )
            assert budget.day(OWNER, end) == expected[-1], f"{context}, day {end}"
        connection.close()
