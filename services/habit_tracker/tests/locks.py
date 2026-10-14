"""Hold the SQLite write lock from outside a service and read through it.

A read path that writes (adoption on a cache hit, an unconditional upsert,
Study's BEGIN IMMEDIATE) waits on the lock for the busy timeout and misses
the deadline. A real read answers at once.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TypeVar

T = TypeVar("T")


@contextmanager
def writer_holding(database: Path) -> Iterator[None]:
    holder = sqlite3.connect(database, isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")
    try:
        yield
    finally:
        holder.execute("ROLLBACK")
        holder.close()


def answered_within(seconds: float, request: Callable[[], T]) -> T:
    answers: list[T] = []
    worker = threading.Thread(target=lambda: answers.append(request()), daemon=True)
    worker.start()
    worker.join(seconds)
    assert answers, (
        f"no answer within {seconds}s while another connection held the write lock"
    )
    return answers[0]
