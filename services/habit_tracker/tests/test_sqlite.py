"""A reused connection behaves like a fresh one for every caller."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from services.habit_tracker.sqlite import connect


def database(path: Path, journal: str = "WAL") -> Path:
    with sqlite3.connect(path) as setup:
        setup.execute(f"PRAGMA journal_mode = {journal}")
        setup.execute("CREATE TABLE notes (value INTEGER)")
        setup.executemany("INSERT INTO notes VALUES (?)", [(1,), (2,), (3,)])
    return path


def test_a_reused_connection_holds_no_snapshot_between_uses(tmp_path: Path) -> None:
    path = database(tmp_path / "notes.db")
    with connect(path) as connection:
        first = connection
        assert connection.execute("SELECT value FROM notes").fetchone() == (1,)
    other = sqlite3.connect(path)
    with other:
        other.execute("UPDATE notes SET value = 10 WHERE value = 1")
    assert other.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0] == 0
    other.close()
    with connect(path) as connection:
        assert connection is first
        assert connection.execute("SELECT min(value) FROM notes").fetchone() == (2,)


def test_a_nested_use_neither_shares_nor_commits_the_outer_transaction(
    tmp_path: Path,
) -> None:
    path = database(tmp_path / "notes.db")
    with connect(path) as idle:
        pass
    with pytest.raises(RuntimeError), connect(path) as outer:
        assert outer is idle
        outer.execute("INSERT INTO notes VALUES (4)")
        with connect(path) as inner:
            assert inner is not outer
            assert inner.execute("SELECT count(*) FROM notes").fetchone() == (3,)
        assert outer.in_transaction
        raise RuntimeError("roll back the outer write")
    with connect(path) as connection:
        assert connection.execute("SELECT count(*) FROM notes").fetchone() == (3,)
        assert not connection.in_transaction


def test_a_failed_use_rolls_back_before_the_connection_is_reused(
    tmp_path: Path,
) -> None:
    path = database(tmp_path / "notes.db")
    with pytest.raises(sqlite3.OperationalError), connect(path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("DELETE FROM notes")
        connection.execute("SELECT missing FROM notes")
    with connect(path) as connection:
        assert not connection.in_transaction
        assert connection.execute("SELECT count(*) FROM notes").fetchone() == (3,)


def test_a_failed_commit_is_not_handed_to_the_next_use(tmp_path: Path) -> None:
    path = database(tmp_path / "notes.db")
    with sqlite3.connect(path) as setup:
        setup.execute("CREATE TABLE parent (id INTEGER PRIMARY KEY)")
        setup.execute("CREATE TABLE child (parent INTEGER REFERENCES parent(id))")
    with pytest.raises(sqlite3.IntegrityError), connect(path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("PRAGMA defer_foreign_keys = ON")
        connection.execute("INSERT INTO child VALUES (1)")
    with connect(path) as connection:
        assert not connection.in_transaction
        assert connection.execute("SELECT count(*) FROM child").fetchone() == (0,)
    with sqlite3.connect(path, timeout=0) as writer:
        writer.execute("INSERT INTO notes VALUES (4)")


def test_a_replaced_database_file_gets_a_new_connection(tmp_path: Path) -> None:
    # A rollback journal: a leftover WAL would be read by any new connection.
    path = database(tmp_path / "notes.db", journal="DELETE")
    with connect(path) as connection:
        assert connection.execute("SELECT count(*) FROM notes").fetchone() == (3,)
    replacement = tmp_path / "replacement.db"
    with sqlite3.connect(replacement) as setup:
        setup.execute("CREATE TABLE notes (value INTEGER)")
    replacement.replace(path)
    with connect(path) as connection:
        assert connection.execute("SELECT count(*) FROM notes").fetchone() == (0,)


def test_a_write_reaches_the_database_file_while_the_connection_stays_open(
    tmp_path: Path,
) -> None:
    path = database(tmp_path / "notes.db")
    with connect(path) as connection:
        connection.execute("INSERT INTO notes VALUES (4)")
    copy = tmp_path / "copy.db"
    copy.write_bytes(path.read_bytes())
    with sqlite3.connect(copy) as backup:
        assert backup.execute("SELECT max(value) FROM notes").fetchone() == (4,)
