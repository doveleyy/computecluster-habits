import sqlite3
from pathlib import Path


def connect(
    database_path: Path,
    *,
    timeout: float = 10,
    row_factory: type[sqlite3.Row] | None = None,
) -> sqlite3.Connection:
    # ``timeout`` is the busy handler: a writer waits this long for the lock.
    connection = sqlite3.connect(database_path, timeout=timeout)
    if row_factory is not None:
        connection.row_factory = row_factory
    connection.execute("PRAGMA foreign_keys = ON")
    # Safe with WAL: a power cut can lose the last commits, never corrupt.
    connection.execute("PRAGMA synchronous = NORMAL")
    return connection
