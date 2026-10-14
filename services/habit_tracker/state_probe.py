"""Hash the records this version reads from SQLite without modifying the database.

Each declared table's rows are projected onto the columns this version reads,
ordered by key and hashed. Columns, tables and indexes that a backward-
compatible migration adds leave the output unchanged; a declared table or
column that is missing fails. A row encodes only its non-NULL values, so a
later release may declare a new nullable column, or a new table that stays
empty until used, without changing the output for existing records.
"""

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import NamedTuple


class Table(NamedTuple):
    name: str
    key: tuple[str, ...]
    columns: tuple[str, ...]


TABLES = (
    Table("users", ("identity",), ("display_name", "created_at")),
    Table("user_settings", ("identity",), ("daily_goal_ml",)),
    Table(
        "drinks",
        ("id",),
        (
            "owner_identity",
            "amount_ml",
            "drink_type",
            "temperature",
            "sweetness",
            "consumed_at",
            "recorded_at",
        ),
    ),
    Table("budget_settings", ("identity",), ("currency", "plan_start_date")),
    Table(
        "daily_budget_changes",
        ("identity", "effective_date"),
        ("amount_cents", "created_at"),
    ),
    Table(
        "budget_adjustments",
        ("id",),
        (
            "owner_identity",
            "effective_date",
            "previous_amount_cents",
            "new_amount_cents",
            "occurred_at",
        ),
    ),
    Table(
        "savings_goal_changes",
        ("sequence",),
        (
            "owner_identity",
            "previous_target_cents",
            "target_cents",
            "currency",
            "occurred_at",
        ),
    ),
    Table(
        "budget_transactions",
        ("id",),
        (
            "owner_identity",
            "kind",
            "amount_cents",
            "description",
            "occurred_at",
            "category",
            "recorded_at",
        ),
    ),
    Table(
        "study_sessions",
        ("id",),
        (
            "owner_identity",
            "activity",
            "duration_minutes",
            "started_at",
            "planned_end_at",
            "ended_at",
            "status",
        ),
    ),
)


ENCODER = json.JSONEncoder(
    sort_keys=True,
    separators=(",", ":"),
    default=lambda value: {"blob": value.hex()},
)


def records(connection: sqlite3.Connection, table: Table) -> dict[str, object]:
    names = (*table.key, *table.columns)
    present = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM pragma_table_info(?)", (table.name,)
        )
    }
    if not present:
        raise ValueError(f"Missing table {table.name}")
    missing = [name for name in names if name not in present]
    if missing:
        raise ValueError(f"Table {table.name} is missing {', '.join(missing)}")
    projection = ", ".join(names)
    count = 0
    digest = hashlib.sha256()
    for row in connection.execute(
        f"SELECT {projection} FROM {table.name} ORDER BY {projection}"
    ):
        raw = ENCODER.encode(
            {
                name: value
                for name, value in zip(names, row, strict=True)
                if value is not None
            }
        ).encode()
        digest.update(str(len(raw)).encode() + b":" + raw)
        count += 1
    return {"rows": count, "sha256": digest.hexdigest()}


def fingerprint(database: Path) -> dict[str, object]:
    uri = database.resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        connection.execute("PRAGMA query_only = ON")
        connection.execute("BEGIN")
        integrity = [row[0] for row in connection.execute("PRAGMA integrity_check")]
        if (
            integrity != ["ok"]
            or connection.execute("PRAGMA foreign_key_check").fetchall()
        ):
            raise ValueError("SQLite integrity or foreign-key check failed")
        tables = {table.name: records(connection, table) for table in TABLES}
        return {
            "tables": {name: table for name, table in tables.items() if table["rows"]}
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    arguments = parser.parse_args()
    print(json.dumps(fingerprint(arguments.database), sort_keys=True))


if __name__ == "__main__":
    main()
