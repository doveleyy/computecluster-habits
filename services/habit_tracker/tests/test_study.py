from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from services.habit_tracker.study import (
    ActiveStudySessionError,
    StudyRepository,
    StudySessionNotActiveError,
)
from services.habit_tracker.tests.members import anonymous, client, owner


def test_study_page_and_identity_boundary(tmp_path: Path) -> None:
    with anonymous(tmp_path / "habits.db") as visitor:
        assert visitor.get("/study").status_code == 401
        assert visitor.get("/api/study/summary").status_code == 401
        assert visitor.get("/api/study/history?days=7").status_code == 401
        assert (
            visitor.post(
                "/api/study/sessions",
                json={"activity": "Math", "duration_minutes": 25},
            ).status_code
            == 401
        )
    with client(tmp_path / "habits.db") as member:
        page = member.get("/study")
        assert page.status_code == 200
        assert 'id="study-link"' in page.text
        assert 'id="countdown"' in page.text
        assert 'id="focus-calendar"' in page.text
        assert member.get("/water").status_code == 200
        assert 'id="study-link"' in member.get("/budget").text
        history = member.get("/api/study/history?days=7")
        assert history.status_code == 200
        assert len(history.json()["days"]) == 7
        assert all(day["total_seconds"] == 0 for day in history.json()["days"])
        assert member.get("/api/study/history?days=32").status_code == 422
        future = date.today() + timedelta(days=365)
        assert member.get(f"/api/study/history?days=1&end={future}").status_code == 422


def test_start_stop_cancel_and_owner_scope(tmp_path: Path) -> None:
    with client(tmp_path / "habits.db") as member:
        invalid = member.post(
            "/api/study/sessions",
            json={"activity": "   ", "duration_minutes": 25},
        )
        invalid_duration = member.post(
            "/api/study/sessions",
            json={"activity": "Math", "duration_minutes": 181},
        )
        started = member.post(
            "/api/study/sessions",
            json={"activity": "  Math  ", "duration_minutes": 25},
        )
        session_id = started.json()["id"]
        duplicate = member.post(
            "/api/study/sessions",
            json={"activity": "Physics", "duration_minutes": 25},
        )
        assert invalid.status_code == 422
        assert invalid_duration.status_code == 422
        assert started.status_code == 201
        assert started.json()["activity"] == "Math"
        assert duplicate.status_code == 409
        assert member.get("/api/study/summary").json()["active"]["id"] == session_id
        with client(tmp_path / "habits.db", "other@example.test") as other:
            assert other.get("/api/study/summary").json()["active"] is None
            assert (
                other.post(f"/api/study/sessions/{session_id}/stop").status_code == 404
            )
        stopped = member.post(f"/api/study/sessions/{session_id}/stop")
        assert stopped.status_code == 200
        assert stopped.json()["status"] == "stopped"
        assert (
            member.post(f"/api/study/sessions/{session_id}/cancel").status_code == 409
        )
        replacement = member.post(
            "/api/study/sessions",
            json={"activity": "Physics", "duration_minutes": 15},
        ).json()
        assert (
            member.post(f"/api/study/sessions/{replacement['id']}/cancel").status_code
            == 204
        )
        summary = member.get("/api/study/summary").json()
        assert summary["active"] is None
        assert [item["activity"] for item in summary["recent"]] == ["Math"]


def test_completion_after_reopen_and_expiry_wins_over_stop(tmp_path: Path) -> None:
    with client(tmp_path / "habits.db") as member:
        assert member.get("/api/study/summary").status_code == 200
    repository = StudyRepository(tmp_path / "habits.db", ZoneInfo("Asia/Singapore"))
    start = datetime(2026, 9, 28, 4, tzinfo=UTC)
    session = repository.start(owner("member@example.test"), "Reading", 25, start)
    reopened = StudyRepository(tmp_path / "habits.db", ZoneInfo("Asia/Singapore"))
    active = reopened.summary(
        owner("member@example.test"), start + timedelta(minutes=10)
    )
    assert active["active"] == session
    completed = reopened.summary(
        owner("member@example.test"), start + timedelta(minutes=30)
    )
    assert completed["active"] is None
    assert completed["today_seconds"] == 1500
    assert completed["recent"][0].status == "completed"
    assert completed["recent"][0].ended_at == session.planned_end_at
    with pytest.raises(StudySessionNotActiveError):
        reopened.finish(
            owner("member@example.test"),
            session.id,
            "stop",
            start + timedelta(minutes=30),
        )


def test_stop_elapsed_time_and_midnight_split(tmp_path: Path) -> None:
    with client(tmp_path / "habits.db") as member:
        assert member.get("/api/study/summary").status_code == 200
    repository = StudyRepository(tmp_path / "habits.db", ZoneInfo("Asia/Singapore"))
    member_key = owner("member@example.test")
    start = datetime(2026, 9, 28, 15, 50, tzinfo=UTC)
    session = repository.start(member_key, "Chemistry", 30, start)
    stopped = repository.finish(
        member_key, session.id, "stop", start + timedelta(minutes=20)
    )
    assert stopped is not None
    assert stopped.elapsed_seconds == 1200
    before_midnight = repository.summary(member_key, start + timedelta(minutes=5))
    after_midnight = repository.summary(member_key, start + timedelta(minutes=21))
    assert before_midnight["today_seconds"] == 600
    assert after_midnight["today_seconds"] == 600
    history = repository.history(
        member_key, date(2026, 9, 29), 2, start + timedelta(minutes=21)
    )
    assert history == [
        {"date": "2026-09-28", "total_seconds": 600},
        {"date": "2026-09-29", "total_seconds": 600},
    ]
    assert [
        day["total_seconds"]
        for day in repository.history(
            owner("other@example.test"),
            date(2026, 9, 29),
            2,
            start + timedelta(minutes=21),
        )
    ] == [0, 0]


def test_concurrent_start_has_one_winner(tmp_path: Path) -> None:
    with client(tmp_path / "habits.db") as member:
        assert member.get("/api/study/summary").status_code == 200
    repository = StudyRepository(tmp_path / "habits.db", ZoneInfo("Asia/Singapore"))
    now = datetime(2026, 9, 28, 4, tzinfo=UTC)

    def start_one() -> str:
        try:
            return repository.start(owner("member@example.test"), "Math", 25, now).id
        except ActiveStudySessionError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: start_one(), range(2)))
    assert results.count("conflict") == 1
    assert len(set(results)) == 2
