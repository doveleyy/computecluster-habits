from __future__ import annotations

import hashlib
from pathlib import Path
from urllib.parse import parse_qsl

from fastapi.middleware.gzip import GZipMiddleware
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

IMMUTABLE_FOR_A_YEAR = "public, max-age=31536000, immutable"


def asset_version(static_dir: Path) -> str:
    """Twelve hex digits of SHA-256 over every file under ``static_dir``.

    Pages link assets as ``?v=<digest>``, so editing, adding, renaming or
    removing a static file changes the URL browsers fetch without a service
    release.
    """
    digest = hashlib.sha256()
    for file in sorted(path for path in static_dir.rglob("*") if path.is_file()):
        digest.update(file.relative_to(static_dir).as_posix().encode())
        digest.update(b"\0")
        digest.update(file.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()[:12]


def text_gzip(app: ASGIApp) -> GZipMiddleware:
    """Compress responses of 1000 bytes or more at level 6.

    Nothing caches compressed output, so every uncached fetch pays again.
    Level 9 costs over twice the CPU of level 6 for under 1% fewer bytes.
    """
    return GZipMiddleware(app, minimum_size=1000, compresslevel=6)


class CacheVersionedAssets:
    """Let browsers keep ``/static/*?v=<version>`` for a year.

    Pages link assets with the running asset version, so a versioned URL
    never changes content. Unversioned, stale-versioned and missing assets
    keep the default revalidation.
    """

    def __init__(self, app: ASGIApp, version: str) -> None:
        self._app = app
        self._version = version

    def _versioned(self, scope: Scope) -> bool:
        if scope["type"] != "http" or not scope["path"].startswith("/static/"):
            return False
        query = scope.get("query_string", b"").decode("latin-1")
        return dict(parse_qsl(query, keep_blank_values=True)).get("v") == self._version

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not self._versioned(scope):
            await self._app(scope, receive, send)
            return

        async def send_cached(message: Message) -> None:
            if message["type"] == "http.response.start" and message["status"] == 200:
                MutableHeaders(scope=message)["Cache-Control"] = IMMUTABLE_FOR_A_YEAR
            await send(message)

        await self._app(scope, receive, send_cached)
