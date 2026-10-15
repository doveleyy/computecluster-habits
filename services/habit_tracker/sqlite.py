import os
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

_idle = threading.local()


@contextmanager
def connect(
    database_path: Path,
    *,
    row_factory: type[sqlite3.Row] | None = None,
) -> Iterator[sqlite3.Connection]:
    """Use a connection as ``with sqlite3.connect(...)`` does: commit or roll back.

    Opening a connection and parsing the schema costs more than most queries,
    so each thread keeps its last connection for the next use. A nested use
    opens its own, and a replaced database file gets a new connection.
    """
    key = (str(database_path), _file_identity(database_path))
    idle: tuple[tuple[str, tuple[int, int] | None], sqlite3.Connection] | None = (
        getattr(_idle, "connection", None)
    )
    _idle.connection = None
    if idle is not None and idle[0] == key:
        connection = idle[1]
    else:
        if idle is not None:
            idle[1].close()
        connection = _open(database_path)
    connection.row_factory = row_factory
    changes = connection.total_changes
    try:
        with connection:
            yield connection
        if connection.total_changes != changes:
            # Closing the last connection used to fold the WAL into the
            # database file; a kept connection must, or file copies miss writes.
            connection.execute("PRAGMA wal_checkpoint(PASSIVE)")
    finally:
        if getattr(_idle, "connection", None) is None:
            _idle.connection = (key, connection)
        else:
            connection.close()


def _open(database_path: Path) -> sqlite3.Connection:
    # The timeout is the busy handler: a writer waits this long for the lock.
    connection = sqlite3.connect(database_path, timeout=10)
    connection.execute("PRAGMA foreign_keys = ON")
    # Safe with WAL: a power cut can lose the last commits, never corrupt.
    connection.execute("PRAGMA synchronous = NORMAL")
    return connection


def _file_identity(database_path: Path) -> tuple[int, int] | None:
    try:
        status = os.stat(database_path)
    except FileNotFoundError:
        return None
    return status.st_dev, status.st_ino
