"""Fail when two things a person reads or presses overlap on a Habits page.

Starts the app on seeded fictional data and drives every page in headless
Chromium at every width, with wider fonts and with zoom. Alex has a year of
history so every chart draws. Sam has none, which draws the empty states. Run it
after any change to `templates/` or `static/`:

    python -m playwright install chromium   # once, or set CHROME to a Chromium
    python -m tools.check_layout
"""

import argparse
import os
import sys
import tempfile
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import uvicorn
from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright
from starlette.types import ASGIApp, Receive, Scope, Send

from services.habit_tracker.main import create_app
from tests.platform_fixture import ALEX_ID, fixture
from tools.seed import HOUSEHOLD, seed

WIDTHS = (1440, 1280, 1024, 900, 840, 820, 720, 600, 414, 390, 375, 360, 320)
STRESS = {
    "default": ("", 1.0),
    "wide-font": ("*{letter-spacing:.06em!important}html{font-size:110%}", 1.0),
    "zoom-125": ("html{zoom:1.25}", 1.25),
}
PAGE_MIN_WIDTH = 280
ALEX = "member@example.test"
SAM = "someone-else@example.test"
BAR = "<!doctype html><body style='margin:0;background:#080808'>"


@dataclass(frozen=True)
class View:
    name: str
    path: str
    setup: str = ""


TODAY = datetime.now(HOUSEHOLD).date()
LAST_WEEK = (TODAY - timedelta(days=TODAY.weekday() + 7)).isoformat()
PAGES = (
    View("overview", ""),
    View("overview goal form", "", "overview goal"),
    View("water", "water"),
    View("water goal form", "water", "water goal"),
    View("budget", "budget"),
    View("budget allowance form", "budget", "budget edit"),
    View("study", "study"),
    View("study active", "study", "focus"),
    View("progress water week", "progress?metric=water&period=week"),
    View(
        "progress water last week",
        f"progress?metric=water&period=week&anchor={LAST_WEEK}",
    ),
    View("progress water month", "progress?metric=water&period=month"),
    View("progress water year", "progress?metric=water&period=year"),
    View("progress spending month", "progress?metric=spending&period=month"),
    View(
        "progress spending last week",
        f"progress?metric=spending&period=week&anchor={LAST_WEEK}",
    ),
    View("progress spending year", "progress?metric=spending&period=year"),
    View("progress focus week", "progress?metric=focus&period=week"),
    View("progress focus month", "progress?metric=focus&period=month"),
    View("progress focus year", "progress?metric=focus&period=year"),
)
EMPTY_PAGES = (
    View("overview", ""),
    View("water", "water"),
    View("budget", "budget"),
    View("study", "study"),
    View("progress water week", "progress?metric=water&period=week"),
    View("progress spending month", "progress?metric=spending&period=month"),
)

SCAN = """
() => {
  const CONTROLS = 'button, input, select, textarea, summary';
  const visible = el => {
    const s = getComputedStyle(el), r = el.getBoundingClientRect();
    return s.visibility !== 'hidden' && s.display !== 'none' && +s.opacity > 0
      && r.width > 0 && r.height > 0;
  };
  const nodes = [...document.querySelectorAll('main *')].filter(el =>
    visible(el) && !el.closest('[hidden]') &&
    (el.matches(CONTROLS + ', a, label') ||
     [...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim())));
  const name = el => {
    const text = (el.innerText || el.textContent || el.value || '').trim()
      .replace(/\\s+/g, ' ').slice(0, 30);
    const raw = el.className && el.className.baseVal !== undefined
      ? el.className.baseVal : el.className;
    const classes = typeof raw === 'string' && raw.trim()
      ? '.' + raw.trim().split(/\\s+/).join('.') : '';
    return `${el.tagName.toLowerCase()}${classes} "${text}"`;
  };
  const painted = el => {
    const r = el.getBoundingClientRect();
    const box = {left: r.left, top: r.top, right: r.right, bottom: r.bottom};
    for (let a = el.parentElement; a; a = a.parentElement) {
      const s = getComputedStyle(a), c = a.getBoundingClientRect();
      if (s.overflowX !== 'visible') {
        box.left = Math.max(box.left, c.left); box.right = Math.min(box.right, c.right);
      }
      if (s.overflowY !== 'visible') {
        box.top = Math.max(box.top, c.top); box.bottom = Math.min(box.bottom, c.bottom);
      }
    }
    return box;
  };
  const boxes = nodes.map(el => [el, painted(el)])
    .filter(([, r]) => r.right > r.left && r.bottom > r.top);
  const problems = [];
  for (let i = 0; i < boxes.length; i++) for (let j = i + 1; j < boxes.length; j++) {
    const [a, ra] = boxes[i], [b, rb] = boxes[j];
    if (a.contains(b) || b.contains(a)) continue;
    const pad = a.matches(CONTROLS) || b.matches(CONTROLS) ? 4 : 0;
    const w = Math.min(ra.right, rb.right) - Math.max(ra.left, rb.left) + pad;
    const h = Math.min(ra.bottom, rb.bottom) - Math.max(ra.top, rb.top) + pad;
    if (w > 1 && h > 1) problems.push(`${name(a)} overlaps ${name(b)}`);
  }
  for (const [el, r] of boxes) {
    const panel = el.parentElement.closest('.panel, .habit-card');
    if (!panel) continue;
    const p = panel.getBoundingClientRect();
    if (r.left < p.left - 1 || r.right > p.right + 1) {
      problems.push(`${name(el)} leaves its panel`);
    }
  }
  const sideways = document.documentElement.scrollWidth
    - document.documentElement.clientWidth;
  if (sideways > 0) problems.push(`page scrolls sideways by ${sideways}px`);
  return [...new Set(problems)];
}
"""

SETTLED = """
() => {
  const q = selector => document.querySelector(selector);
  if (!q('#user')?.textContent) return false;
  if (q('#app')?.getAttribute('aria-busy') === 'true') return false;
  if (q('.stats-chart') && !q('.stats-chart svg *')) return false;
  const waiting = [...document.querySelectorAll('strong[id], .big, .total')];
  return !waiting.some(el => el.textContent.trim() === '—' && !el.closest('[hidden]'));
}
"""


class Gateway:
    """Strip the base path before forwarding, as the platform gateway does."""

    def __init__(self, app: ASGIApp, base: str) -> None:
        self.app, self.base = app, base

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["path"].startswith(self.base):
            scope = {**scope, "path": scope["path"][len(self.base) :] or "/"}
        await self.app(scope, receive, send)


@contextmanager
def serve(directory: Path) -> Iterator[str]:
    """Run the app on seeded data and yield its origin with the base path."""
    os.environ["TZ"] = "Asia/Singapore"
    os.environ.pop("APP_STATE_DIR", None)
    database = directory / "habit-tracker.db"
    today = datetime.now(HOUSEHOLD).date()
    seed(database, f"home-platform:{ALEX_ID}", "Alex", today)
    with fixture() as core:
        app = Gateway(create_app(database, platform=core.platform), "/habits")
        server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
        )
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        while not server.started:
            time.sleep(0.05)
        port = server.servers[0].sockets[0].getsockname()[1]
        try:
            yield f"http://127.0.0.1:{port}/habits/"
        finally:
            server.should_exit = True
            thread.join()


def new_context(browser: Browser, member: str) -> BrowserContext:
    context = browser.new_context(
        extra_http_headers={"X-Platform-User-Context": member}
    )
    context.route(
        "**/platform/bar*",
        lambda route: route.fulfill(content_type="text/html", body=BAR),
    )
    return context


def settle(page: Page) -> None:
    page.wait_for_load_state("networkidle")
    page.wait_for_function(SETTLED)
    page.wait_for_timeout(250)


@contextmanager
def visit(
    page: Page, context: BrowserContext, origin: str, view: View
) -> Iterator[int]:
    """Open a view in the state its name describes and yield the response status."""
    started = None
    if view.setup == "focus":
        started = context.request.post(
            origin + "api/study/sessions",
            data={
                "activity": "Practice exam for the history unit",
                "duration_minutes": 45,
            },
        )
    response = page.goto(origin + view.path)
    status = response.status if response else 0
    settle(page)
    triggers = {
        "overview goal": "#goal-trigger",
        "water goal": "#goal-trigger",
        "budget edit": "#budget-trigger",
    }
    if view.setup in triggers:
        page.click(triggers[view.setup])
        page.wait_for_timeout(100)
    try:
        yield status
    finally:
        if started is not None:
            identifier = started.json()["id"]
            context.request.post(origin + f"api/study/sessions/{identifier}/cancel")


def scan(origin: str, widths: tuple[int, ...], modes: tuple[str, ...]) -> list[str]:
    problems: list[str] = []
    scanned = 0
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=os.environ.get("CHROME") or None
        )
        for member, views in ((ALEX, PAGES), (SAM, EMPTY_PAGES)):
            context = new_context(browser, member)
            who = "alex" if member == ALEX else "sam"
            for mode, (css, zoom) in STRESS.items():
                if mode not in modes:
                    continue
                for width in widths:
                    if width / zoom < PAGE_MIN_WIDTH:
                        continue
                    page = context.new_page()
                    page.set_viewport_size({"width": width, "height": 900})
                    for view in views:
                        with visit(page, context, origin, view) as status:
                            if status != 200:
                                problems.append(f"{who} {view.name} answered {status}")
                            if css:
                                page.add_style_tag(content=css)
                            problems += [
                                f"{who} {mode} {width}px {view.name}: {problem}"
                                for problem in page.evaluate(SCAN)
                            ]
                        scanned += 1
                    page.close()
            context.close()
        browser.close()
    print(f"{scanned} page views scanned")
    return problems


def main(arguments: list[str]) -> int:
    options = argparse.ArgumentParser(description=__doc__)
    options.add_argument("--widths", default=",".join(map(str, WIDTHS)))
    options.add_argument("--modes", default=",".join(STRESS))
    given = options.parse_args(arguments)
    widths = tuple(int(width) for width in given.widths.split(","))
    modes = tuple(given.modes.split(","))
    with tempfile.TemporaryDirectory() as directory, serve(Path(directory)) as origin:
        problems = scan(origin, widths, modes)
    for problem in problems:
        print(problem)
    print(f"{len(problems)} layout problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
