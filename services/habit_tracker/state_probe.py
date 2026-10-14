"""Hash every SQLite schema entry and stored row without modifying the database."""

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path


def encoded(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


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
        schema = connection.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name"
        ).fetchall()
        tables: dict[str, object] = {}
        for kind, name, _, _ in schema:
            if kind != "table":
                continue
            quoted = '"' + name.replace('"', '""') + '"'
            rows = []
            for row in connection.execute("SELECT * FROM " + quoted):
                values = [
                    {"blob": value.hex()} if isinstance(value, bytes) else value
                    for value in row
                ]
                rows.append(encoded(values))
            digest = hashlib.sha256()
            for raw in sorted(rows):
                digest.update(str(len(raw)).encode() + b":" + raw)
            tables[name] = {"rows": len(rows), "sha256": digest.hexdigest()}
        summary = {
            "schema_sha256": hashlib.sha256(encoded(schema)).hexdigest(),
            "tables": tables,
        }
        return {**summary, "state_sha256": hashlib.sha256(encoded(summary)).hexdigest()}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    arguments = parser.parse_args()
    print(json.dumps(fingerprint(arguments.database), sort_keys=True))


if __name__ == "__main__":
    main()
