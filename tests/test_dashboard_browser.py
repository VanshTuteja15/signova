"""Drive the real dashboard in Chrome (Playwright): every tab, a sentence in sim mode, screenshots.

Uses the installed Google Chrome (channel="chrome"); falls back to Playwright's Chromium if it
was installed with `python -m playwright install chromium`. Skips if neither is available.
Screenshots are written to docs/screenshots/.
"""

from __future__ import annotations

import re
import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
import uvicorn

from signova.config import ROOT, HandConfig
from signova.library import Library
from signova.server import create_app

pytestmark = pytest.mark.browser
playwright = pytest.importorskip("playwright.sync_api")
SHOTS = ROOT / "docs" / "screenshots"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


APP: dict[str, object] = {}


@pytest.fixture
def server(hand: HandConfig, library: Library, tmp_path: Path) -> Iterator[str]:
    app = create_app(hand, library, data_dir=tmp_path / "data", models_dir=tmp_path / "models", mode="sim")
    APP["app"] = app
    port = free_port()
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    deadline = time.time() + 15
    while not srv.started:
        if time.time() > deadline:
            raise RuntimeError("server did not start")
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True
    thread.join(timeout=5)


@pytest.fixture
def page(server: str):
    with playwright.sync_playwright() as p:
        browser = None
        for kwargs in ({"channel": "chrome"}, {}):
            try:
                browser = p.chromium.launch(headless=True, **kwargs)
                break
            except Exception:
                continue
        if browser is None:
            pytest.skip("No Chrome/Chromium available for Playwright")
        ctx = browser.new_context(viewport={"width": 1440, "height": 1000}, device_scale_factor=1)
        pg = ctx.new_page()
        pg.errors = []  # type: ignore[attr-defined]
        pg.on("console", lambda m: pg.errors.append(m.text) if m.type == "error" else None)  # type: ignore[attr-defined]
        pg.on("pageerror", lambda e: pg.errors.append(str(e)))  # type: ignore[attr-defined]
        pg.goto(server)
        pg.wait_for_selector("#ws-pill.ok", timeout=15000)
        yield pg
        ctx.close()
        browser.close()


def shot(page, name: str) -> None:
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / name))


def say(page, text: str) -> None:
    page.fill("#say-text", text)
    page.click("#say-btn")


def wait_done(page, timeout: float = 30000) -> None:
    page.wait_for_function(
        "() => /done|nothing to sign/.test(document.querySelector('#now-sub').textContent)", timeout=timeout
    )


def set_range(page, selector: str, value: float) -> None:
    page.eval_on_selector(
        selector,
        "(el, v) => { el.value = String(v); el.dispatchEvent(new Event('input', {bubbles: true})); }",
        value,
    )


def test_dashboard_end_to_end(page) -> None:
    pg = page
    # ---------------------------------------------------------------- Live
    assert pg.is_visible("#sim-banner")
    assert "simulation" in pg.inner_text("#hand-pill")
    say(pg, "I love you")
    pg.wait_for_function("() => document.querySelector('#now-sign').textContent === 'ILY'", timeout=10000)
    wait_done(pg)
    assert pg.inner_text("#gloss .tok.sign") == "ILY"
    assert "first pose" in pg.inner_text("#latency")
    pg.wait_for_timeout(300)
    shot(pg, "live_i_love_you.png")

    say(pg, "code is so cool")
    pg.wait_for_function("() => document.querySelectorAll('#gloss .tok').length === 4", timeout=10000)
    toks = pg.eval_on_selector_all(
        "#gloss .tok", "els => els.map(e => e.className.replace(' now', '') + '|' + e.firstChild.textContent)"
    )
    assert toks == ["tok fs|FS:CODE", "tok drop|is", "tok fs|FS:SO", "tok fs|FS:COOL"]
    pg.wait_for_function("() => document.querySelector('#now-sign').textContent === 'D'", timeout=10000)
    pg.wait_for_timeout(350)
    shot(pg, "live_fingerspelling.png")
    wait_done(pg, 40000)
    letters = pg.eval_on_selector_all(
        "#log .tx", 'els => els.filter(e => e.textContent.includes(\'"cmd":"pose"\')).length'
    )
    assert letters >= 12  # 1 (ILY) + 10 letters + 1 bounce

    # ---------------------------------------------------------------- Pose Studio: create a sign, then use it
    pg.click("#tab-studio")
    pg.select_option("#studio-load", "ILY")
    assert pg.input_value("#st-id") == "ILY"
    pg.select_option("#studio-load", "")
    pg.fill("#st-id", "ROCK")
    pg.fill("#st-english", "rock on")
    pg.fill("#st-notes", "test sign from Pose Studio")
    for joint, v in {
        "thumb": 0.7,
        "thumb_rot": 0.9,
        "index": 0.0,
        "middle": 1.0,
        "ring": 1.0,
        "pinky": 0.0,
    }.items():
        set_range(pg, f"#sl-{joint}", v)
    assert pg.inner_text("output[for='sl-middle']") == "1.00"
    pg.click("#frame-add")
    set_range(pg, "#sl-wrist", 0.8)
    pg.click("#st-save")
    pg.wait_for_function(
        "() => /Saved ROCK/.test(document.querySelector('#st-status').textContent)", timeout=10000
    )
    pg.wait_for_timeout(300)
    shot(pg, "pose_studio.png")
    pg.click("#tab-live")
    say(pg, "rock on")
    pg.wait_for_function(
        "() => [...document.querySelectorAll('#gloss .tok.sign')].some(t => t.textContent === 'ROCK')",
        timeout=10000,
    )
    wait_done(pg)

    # ---------------------------------------------------------------- Library
    pg.click("#tab-lib")
    assert pg.is_visible("article[aria-label='Sign ROCK']")
    assert "unavailable" in (pg.get_attribute("article[aria-label='Sign U']", "class") or "")
    shot(pg, "library.png")

    # ---------------------------------------------------------------- Calibration with the emulator
    pg.click("#tab-cal")
    pg.select_option("#mode-select", "emulator")
    pg.click("#connect-btn")
    pg.wait_for_function(
        "() => /Connected/.test(document.querySelector('#conn-status').textContent)", timeout=15000
    )
    assert "emulator" in pg.inner_text("#hand-pill")
    pg.wait_for_selector(".cal-row")
    rows = pg.locator(".cal-row")
    index_row = rows.nth(2)
    set_range(pg, "#raw-index", 700)
    index_row.get_by_role("button", name="Set min").click()
    set_range(pg, "#raw-index", 2200)
    index_row.get_by_role("button", name="Set max").click()
    index_row.get_by_role("button", name="Save to ESP32").click()
    pg.wait_for_function(
        "() => /min 700 · max 2200/.test(document.querySelectorAll('.cal-row')[2].textContent)", timeout=10000
    )
    pg.click("#cal-load")
    pg.wait_for_function(
        "() => /min 700 · max 2200/.test(document.querySelectorAll('.cal-row')[2].textContent)", timeout=10000
    )
    shot(pg, "calibration.png")
    pg.click("#tab-live")
    say(pg, "I love you")
    pg.wait_for_function("() => document.querySelector('#now-sign').textContent === 'ILY'", timeout=10000)
    wait_done(pg)
    assert any(
        '"done"' in t for t in pg.eval_on_selector_all("#log .rx", "els => els.map(e => e.textContent)")
    )
    pg.click("#tab-cal")
    pg.select_option("#mode-select", "sim")
    pg.click("#connect-btn")
    pg.wait_for_function(
        "() => /simulation/.test(document.querySelector('#hand-pill').textContent)", timeout=10000
    )

    # ---------------------------------------------------------------- Evaluation (3 signs)
    pg.click("#tab-eval")
    pg.fill("#ev-count", "5")
    pg.fill("#ev-participant", "P1")
    pg.click("#ev-start")
    pg.wait_for_selector("#ev-next:not([disabled])")
    # The test peeks at the server's hidden order to simulate a signer who gets 4 of 5 right.
    session = APP["app"].state.signova.eval  # type: ignore[attr-defined]
    answers = [t.sign_id for t in session.trials]
    for i, sid in enumerate(answers):
        pg.wait_for_selector("#ev-next:not([disabled])")
        pg.click("#ev-next")
        pg.wait_for_selector("#ev-guess:not([disabled])")
        assert pg.inner_text("#now-sign") in ("?", "—", "ILY", "W")  # live view shows "?" during evaluation
        pg.fill("#ev-guess", "not sure" if i == 1 else sid.lower())
        pg.click("#ev-answer")
    pg.wait_for_selector("#ev-results:not([hidden])", timeout=10000)
    assert pg.inner_text("#ev-overall") == "80%"
    assert pg.locator("#ev-table tbody tr").count() == len(set(answers))
    assert re.search(r"\.csv", pg.inner_text("#ev-saved"))
    shot(pg, "evaluation.png")

    # ---------------------------------------------------------------- About + dark theme
    pg.click("#tab-about")
    assert "Not a translator" in pg.inner_text("#panel-about")
    shot(pg, "about.png")
    pg.click("#tab-live")
    pg.click("#theme-btn")
    say(pg, "Lisa is so wise")
    pg.wait_for_function("() => document.querySelector('#now-sign').textContent === 'W'", timeout=20000)
    pg.wait_for_timeout(400)
    shot(pg, "live_dark.png")
    wait_done(pg, 40000)

    assert pg.errors == []  # type: ignore[attr-defined]
