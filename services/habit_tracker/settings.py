import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


@dataclass(frozen=True)
class Settings:
    database_path: Path
    base_path: str
    timezone: ZoneInfo


def load_settings() -> Settings:
    retired = {
        "HABIT_TRACKER_ALLOW_DEV_IDENTITY",
        "HABIT_TRACKER_IDENTITY_RESOLVER_URL",
        "HABIT_TRACKER_IDENTITY_TOKEN_FILE",
        "HABIT_TRACKER_DB_PATH",
        "HABIT_TRACKER_BASE_PATH",
        "HABIT_TRACKER_TIMEZONE",
        "APP_ALLOW_DEV_IDENTITY",
        "HOME_PLATFORM_DEV_USER",
    }
    if retired.intersection(os.environ) or any(
        key.startswith("HP_FIXTURE_") for key in os.environ
    ):
        raise RuntimeError("Use the managed runtime contract, not legacy settings")
    base_path = os.environ.get("APP_BASE_PATH", "/habits").rstrip("/")
    if base_path and (
        not base_path.startswith("/")
        or "//" in base_path
        or any(part in {".", ".."} for part in base_path.split("/"))
        or any(character in base_path for character in "?#\\")
    ):
        raise RuntimeError("APP_BASE_PATH must be an absolute URL path")
    timezone_name = os.environ.get("TZ", "UTC")
    try:
        timezone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as error:
        raise RuntimeError("TZ must name an installed timezone") from error
    return Settings(
        Path(os.environ.get("APP_STATE_DIR", "data")) / "habit-tracker.db",
        base_path,
        timezone,
    )
