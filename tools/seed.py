"""Fictional household data for the layout checker and screenshots.

Alex gets a year of drinks, spending and focus sessions so every chart draws.
Sam gets nothing, which draws every empty state.
"""

import random
import sqlite3
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from services.habit_tracker.budget import BudgetRepository
from services.habit_tracker.study import StudyRepository
from services.habit_tracker.water import WaterRepository

HOUSEHOLD = ZoneInfo("Asia/Singapore")
DAYS = 400
DRINKS = (
    ("water", 0.5),
    ("supplement_water", 0.05),
    ("coffee", 0.14),
    ("tea", 0.1),
    ("milk", 0.05),
    ("protein_shake", 0.04),
    ("juice", 0.04),
    ("soft_drink", 0.03),
    ("sports_drink", 0.03),
    ("alcohol", 0.01),
    ("other", 0.01),
)
SPENDING = (
    ("food", 640, "Lunch at the market"),
    ("drinks", 520, "Iced tea"),
    ("transport", 310, "Train fare"),
    ("shopping", 1800, "Notebook"),
    ("bills", 2400, "Phone plan"),
    ("entertainment", 1500, "Cinema"),
    ("health", 900, "Pharmacy"),
    ("other", 450, "Stationery"),
)
ACTIVITIES = (
    "Linear algebra problem set",
    "Reading the history chapter",
    "Thesis outline",
    "Vocabulary review",
    "Practice exam",
)


def stamp(day: date, hour: float) -> datetime:
    local = datetime.combine(day, time(), HOUSEHOLD) + timedelta(hours=hour)
    return local.astimezone(UTC)


def seed(database: Path, owner: str, name: str, today: date) -> None:
    """Write a year of fictional history for `owner`, ending today."""
    water = WaterRepository(database, HOUSEHOLD)
    budget = BudgetRepository(database, HOUSEHOLD)
    study = StudyRepository(database, HOUSEHOLD)
    for repository in (water, budget, study):
        repository.initialize()
    water.ensure_user(owner, name)
    first = today - timedelta(days=DAYS)
    budget.ensure_user(owner, first)
    budget.set_daily_budget(owner, first + timedelta(days=1), 2500)
    budget.set_daily_budget(owner, today - timedelta(days=90), 2800)
    budget.set_savings_goal(owner, 150000)
    water.set_goal(owner, 2200)
    rng = random.Random(7)
    now = datetime.now(UTC)
    kinds = [kind for kind, _ in DRINKS]
    weights = [weight for _, weight in DRINKS]
    with sqlite3.connect(database) as connection:
        for back in range(DAYS, -1, -1):
            day = today - timedelta(days=back)
            quiet = rng.random() < 0.06 and back > 0
            if not quiet:
                target = 2600 if back % 5 == 0 else rng.randint(1100, 2700)
                if back == 0:
                    target = 1450
                total, entries = 0, 0
                while total < target and entries < 14:
                    amount = rng.choice((150, 200, 250, 300, 350, 500))
                    when = stamp(day, rng.uniform(6.5, 22.5))
                    if back == 0:
                        when = min(when, now - timedelta(minutes=5 * (entries + 1)))
                    kind = rng.choices(kinds, weights)[0]
                    sweet = rng.choice(("none", "less", "regular", "extra"))
                    connection.execute(
                        "INSERT INTO drinks(id, owner_identity, amount_ml, drink_type,"
                        " temperature, sweetness, consumed_at, recorded_at)"
                        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            str(uuid4()),
                            owner,
                            amount,
                            kind,
                            rng.choice(("hot", "normal", "normal", "iced")),
                            sweet
                            if kind in {"coffee", "tea", "soft_drink", "sports_drink"}
                            else None,
                            when.isoformat(),
                            when.isoformat(),
                        ),
                    )
                    total += amount
                    entries += 1
            if rng.random() < 0.9:
                for _ in range(rng.randint(1, 4)):
                    category, base, note = rng.choice(SPENDING)
                    when = stamp(day, rng.uniform(8, 21))
                    if back == 0:
                        when = min(when, now - timedelta(minutes=30))
                    cents = int(base * rng.uniform(0.6, 1.5))
                    connection.execute(
                        "INSERT INTO budget_transactions(id, owner_identity, kind,"
                        " amount_cents, description, occurred_at, category,"
                        " recorded_at) VALUES (?, ?, 'daily_spend', ?, ?, ?, ?, ?)",
                        (
                            str(uuid4()),
                            owner,
                            cents,
                            note,
                            when.isoformat(),
                            category,
                            when.isoformat(),
                        ),
                    )
            if back % 30 == 12:
                when = stamp(day, 10)
                connection.execute(
                    "INSERT INTO budget_transactions(id, owner_identity, kind,"
                    " amount_cents, description, occurred_at, category, recorded_at)"
                    " VALUES (?, ?, 'fund_contribution', 6000, 'Monthly top-up', ?,"
                    " 'other', ?)",
                    (str(uuid4()), owner, when.isoformat(), when.isoformat()),
                )
    for back in range(DAYS, -1, -1):
        day = today - timedelta(days=back)
        if rng.random() < 0.18:
            continue
        for _ in range(rng.randint(1, 3)):
            begin = stamp(day, rng.uniform(8, 20.5))
            minutes = rng.choice((25, 25, 45, 50, 90))
            if back == 0:
                begin = min(begin, now - timedelta(minutes=minutes + 40))
            session = study.start(owner, rng.choice(ACTIVITIES), minutes + 5, now=begin)
            study.finish(
                owner, session.id, "stop", now=begin + timedelta(minutes=minutes)
            )
