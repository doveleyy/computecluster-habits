import re
import sqlite3
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from hp_client import Platform
from starlette.routing import Mount

from services.habit_tracker.main import create_app
from services.habit_tracker.water import WaterRepository
from tests.platform_fixture import ALEX_ID, SAM_ID, fixture

ALEX = {"X-Platform-User-Context": "member@example.test"}
SAM = {"X-Platform-User-Context": "someone-else@example.test"}
PAGES = ("/", "/water", "/budget", "/study", "/progress")
READS = (
    "/api/water/today",
    "/api/budget/summary",
    "/api/study/summary",
    "/api/progress",
)
OPEN_PATHS = {"/health", "/ready", "/version", "/static"}


def users(database: Path) -> list[tuple[str, str]]:
    with sqlite3.connect(database) as connection:
        return connection.execute(
            "SELECT identity, display_name FROM users ORDER BY identity"
        ).fetchall()


def test_two_members_each_see_only_their_own_records(tmp_path: Path) -> None:
    database = tmp_path / "habit-tracker.db"
    with (
        fixture() as core,
        TestClient(create_app(database, platform=core.platform)) as browser,
    ):
        drink = browser.post(
            "/api/water/drinks", json={"amount_ml": 500}, headers=ALEX
        ).json()
        assert (
            browser.post(
                "/api/budget/transactions",
                json={"kind": "daily_spend", "amount_cents": 450},
                headers=ALEX,
            ).status_code
            == 201
        )
        assert browser.get("/api/water/today", headers=SAM).json()["total_ml"] == 0
        sam_budget = browser.get("/api/budget/summary", headers=SAM).json()
        assert sam_budget["daily_spent_cents"] == 0
        assert (
            browser.delete(f"/api/water/drinks/{drink['id']}", headers=SAM).status_code
            == 404
        )
        assert browser.get("/api/water/today", headers=ALEX).json()["total_ml"] == 500
        calls = core.calls
        browser.get("/api/water/today", headers=ALEX)
        browser.get("/api/water/today", headers=ALEX)
        assert core.calls == calls + 2, "every request is verified by the core"
    assert users(database) == [
        (f"home-platform:{ALEX_ID}", "Alex"),
        (f"home-platform:{SAM_ID}", "Sam"),
    ]


def test_missing_forged_and_raw_legacy_identity_is_denied(tmp_path: Path) -> None:
    database = tmp_path / "habit-tracker.db"
    with (
        fixture() as core,
        TestClient(create_app(database, platform=core.platform)) as browser,
    ):
        for headers in (
            {},
            {"X-Platform-User-Context": "forged"},
            {"Tailscale-User-Login": "member@example.test"},
            {"X-Habit-Tracker-Dev-User": "member@example.test"},
        ):
            for route in PAGES + READS:
                assert browser.get(route, headers=headers).status_code == 401, route
            assert (
                browser.post(
                    "/api/water/drinks", json={"amount_ml": 250}, headers=headers
                ).status_code
                == 401
            )
        assert browser.get("/health").status_code == 200
        assert browser.get("/ready").status_code == 200
        assert browser.get("/version").json() == {
            "service": "habits",
            "version": "0.13.1",
        }
    assert users(database) == []


def test_every_route_but_the_open_ones_requires_identity(tmp_path: Path) -> None:
    database = tmp_path / "habit-tracker.db"
    with fixture() as core:
        app = create_app(database, platform=core.platform)
        private = [
            (method, re.sub(r"\{[^}]+\}", "fictional", route.path))
            for route in app.routes
            if route.path not in OPEN_PATHS
            for method in ({"GET"} if isinstance(route, Mount) else route.methods)
        ]
        assert private
        with TestClient(app) as anonymous:
            for method, path in private:
                status = anonymous.request(method, path).status_code
                assert status == 401, f"{method} {path} answered {status}"
    assert users(database) == []


def test_revoked_and_unlinked_identities_keep_the_core_refusal(
    tmp_path: Path,
) -> None:
    database = tmp_path / "habit-tracker.db"
    with (
        fixture() as core,
        TestClient(create_app(database, platform=core.platform)) as browser,
    ):
        assert browser.get("/api/water/today", headers=ALEX).status_code == 200
        core.status = 403
        for route in PAGES + READS:
            assert browser.get(route, headers=ALEX).status_code == 403, route
        assert browser.get("/api/water/today", headers=SAM).status_code == 403
        core.status = None
        assert browser.get("/api/water/today", headers=ALEX).status_code == 200
    assert users(database) == [(f"home-platform:{ALEX_ID}", "Alex")]


def test_unavailable_core_fails_closed_without_creating_an_owner(
    tmp_path: Path,
) -> None:
    database = tmp_path / "habit-tracker.db"
    platform = Platform(
        "http://127.0.0.1:1/api/apps/v1", "fictional-app-key", timeout=1
    )
    with TestClient(create_app(database, platform=platform)) as browser:
        for route in PAGES + READS:
            assert browser.get(route, headers=ALEX).status_code == 503, route
    with fixture() as core:
        core.status = 500
        with TestClient(create_app(database, platform=core.platform)) as browser:
            assert browser.get("/api/water/today", headers=ALEX).status_code == 503
    assert users(database) == []


def test_stable_owner_keeps_existing_records_and_follows_display_name(
    tmp_path: Path,
) -> None:
    database = tmp_path / "habit-tracker.db"
    stable = f"home-platform:{ALEX_ID}"
    water = WaterRepository(database, ZoneInfo("Asia/Singapore"))
    water.initialize()
    water.ensure_user(stable, "Previous name")
    water.set_goal(stable, 2750)
    water.add_drink(stable, 300, "tea", "hot", "less")
    with (
        fixture() as core,
        TestClient(create_app(database, platform=core.platform)) as browser,
    ):
        today = browser.get("/api/water/today", headers=ALEX).json()
        assert today["goal_ml"] == 2750
        assert today["breakdown_ml"] == {"tea": 300}
        assert users(database) == [(stable, "Alex")]
        core.users["member@example.test"] = (ALEX_ID, "Alex Renamed")
        assert browser.get("/api/water/today", headers=ALEX).status_code == 200
    assert users(database) == [(stable, "Alex Renamed")]
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT DISTINCT owner_identity FROM drinks"
        ).fetchall() == [(stable,)]
        assert connection.execute(
            "SELECT identity FROM budget_settings"
        ).fetchall() == [(stable,)]


def test_existing_owner_reads_change_no_stored_data(tmp_path: Path) -> None:
    database = tmp_path / "habit-tracker.db"
    with (
        fixture() as core,
        TestClient(create_app(database, platform=core.platform)) as browser,
    ):
        assert browser.get("/api/water/today", headers=ALEX).status_code == 200
        watcher = sqlite3.connect(database)
        version = watcher.execute("PRAGMA data_version").fetchone()
        for route in READS:
            assert browser.get(route, headers=ALEX).status_code == 200
        assert watcher.execute("PRAGMA data_version").fetchone() == version
        watcher.close()


@pytest.mark.parametrize(
    "setting",
    [
        "HABIT_TRACKER_ALLOW_DEV_IDENTITY",
        "HABIT_TRACKER_IDENTITY_RESOLVER_URL",
        "HABIT_TRACKER_DB_PATH",
        "APP_ALLOW_DEV_IDENTITY",
        "HOME_PLATFORM_DEV_USER",
        "HP_FIXTURE_USERS",
    ],
)
def test_legacy_runtime_switches_are_refused(
    monkeypatch: pytest.MonkeyPatch,
    setting: str,
) -> None:
    monkeypatch.setenv(setting, "false")
    with pytest.raises(RuntimeError, match="managed runtime contract"):
        create_app()
