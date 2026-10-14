import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

from hp_client import Platform

ALEX_ID = "00000000-0000-4000-8000-000000000001"
SAM_ID = "00000000-0000-4000-8000-000000000002"


@dataclass
class Fixture:
    users: dict[str, tuple[str, str]] = field(
        default_factory=lambda: {
            "member@example.test": (ALEX_ID, "Alex"),
            "someone-else@example.test": (SAM_ID, "Sam"),
        }
    )
    status: int | None = None
    calls: int = 0
    platform: Platform | None = None


@contextmanager
def fixture() -> Iterator[Fixture]:
    state = Fixture()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            pass

        def do_GET(self) -> None:
            state.calls += 1
            assertion = self.headers.get("X-Platform-User-Context", "")
            identity = state.users.get(assertion)
            if self.path != "/api/apps/v1/user":
                status, value = 404, {"detail": "Missing route"}
            elif self.headers.get("Authorization") != "Bearer fictional-app-key":
                status, value = 401, {"detail": "Wrong app credential"}
            elif state.status is not None:
                status, value = state.status, {"detail": "Refused"}
            elif identity is None:
                status, value = 401, {"detail": "Wrong context"}
            else:
                status, value = 200, {"id": identity[0], "display_name": identity[1]}
            raw = json.dumps(value).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    # The default half-second shutdown poll dominates a suite of short tests.
    thread = Thread(target=server.serve_forever, args=(0.05,), daemon=True)
    thread.start()
    state.platform = Platform(
        f"http://127.0.0.1:{server.server_port}/api/apps/v1",
        "fictional-app-key",
        timeout=1,
    )
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
