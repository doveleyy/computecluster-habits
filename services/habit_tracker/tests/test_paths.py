from pathlib import Path

from fastapi.testclient import TestClient
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from services.habit_tracker.main import create_app
from tests.platform_fixture import fixture

PREFIX = "/habits"


def behind_serve(app: ASGIApp) -> ASGIApp:
    """Tailscale Serve: strip ``/habits``; other paths belong to another backend."""

    async def proxy(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await app(scope, receive, send)
            return
        path = scope["path"]
        if path != PREFIX and not path.startswith(PREFIX + "/"):
            await PlainTextResponse("not habits", 404)(scope, receive, send)
            return
        stripped = path[len(PREFIX) :] or "/"
        await app(
            {**scope, "path": stripped, "raw_path": stripped.encode()}, receive, send
        )

    return proxy


def test_trailing_slash_page_urls_land_on_the_page_inside_habits(
    tmp_path: Path,
) -> None:
    with (
        fixture() as core,
        TestClient(
            behind_serve(create_app(tmp_path / "habits.db", platform=core.platform)),
            headers={"X-Platform-User-Context": "member@example.test"},
        ) as browser,
    ):
        for page in ("water", "budget", "study"):
            response = browser.get(f"{PREFIX}/{page}/?from=bookmark")

            assert response.status_code == 200, response.text
            assert response.url.path == f"{PREFIX}/{page}"
            assert response.url.query == b"from=bookmark"
            assert f'id="{page}-link" href="{PREFIX}/{page}" aria-current="page"' in (
                response.text
            )


def test_unknown_trailing_slash_path_is_not_redirected(tmp_path: Path) -> None:
    with (
        fixture() as core,
        TestClient(
            behind_serve(create_app(tmp_path / "habits.db", platform=core.platform))
        ) as browser,
    ):
        response = browser.get(f"{PREFIX}/nowhere/", follow_redirects=False)

    assert response.status_code == 404
