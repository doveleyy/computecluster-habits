"""Per-request cost: study reads never take the write lock."""

from __future__ import annotations

from pathlib import Path

from services.habit_tracker.tests.locks import answered_within, writer_holding
from services.habit_tracker.tests.members import client


def test_study_reads_answer_while_another_writer_holds_the_lock(
    tmp_path: Path,
) -> None:
    database = tmp_path / "habits.db"
    # Every request resolves the member through the core and ensures their rows.
    with client(database) as member:
        started = member.post(
            "/api/study/sessions", json={"activity": "Reading", "duration_minutes": 25}
        )
        assert started.status_code == 201
        with writer_holding(database):
            summary = answered_within(2, lambda: member.get("/api/study/summary"))
            history = answered_within(
                2, lambda: member.get("/api/study/history?days=7")
            )
    assert summary.status_code == 200
    assert summary.json()["active"]["activity"] == "Reading"
    assert history.status_code == 200
    assert len(history.json()["days"]) == 7
