"""Test clients that sign in through the real client SDK and a fixture core."""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from fastapi.testclient import TestClient

from services.habit_tracker.main import create_app
from tests.platform_fixture import fixture


def owner(user: str) -> str:
    return f"home-platform:{uuid5(NAMESPACE_URL, user)}"


@contextmanager
def client(
    database: Path, user: str = "member@example.test", **options: Any
) -> Iterator[TestClient]:
    with fixture() as core:
        core.users[user] = (owner(user).removeprefix("home-platform:"), user)
        with TestClient(
            create_app(database, platform=core.platform),
            headers={"X-Platform-User-Context": user},
            **options,
        ) as connected:
            yield connected


@contextmanager
def anonymous(database: Path) -> Iterator[TestClient]:
    with (
        fixture() as core,
        TestClient(create_app(database, platform=core.platform)) as connected,
    ):
        yield connected
