"""Headless Google Chrome tests of a built site, served with the CSP CloudFront sends (design/v0/v0.4.1.md).

They need Playwright and Google Chrome (pip install -e ".[browser]"; CI also runs playwright install chrome). Without
them the module is skipped, unless SOSFORGE_BROWSER_TESTS=1, as in CI, makes a missing browser a failure.
"""
from __future__ import annotations

import functools
import http.server
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import cv2
import numpy as np
import pytest

from sosforge import site
from test_site import make_dataset

REQUIRED = os.environ.get("SOSFORGE_BROWSER_TESTS") == "1"
try:
    from playwright.sync_api import sync_playwright
except ImportError:
    if REQUIRED:
        raise
    pytest.skip("Playwright is not installed (pip install -e '.[browser]')", allow_module_level=True)

sys.path.insert(0, str(site.REPO_ROOT / "tools"))
from smoke import download_urls, watch  # noqa: E402

CONFIG = '''[site]
domain = "test.example"
[[datasets]]
slug = "tiny"
release = "data-1"
preview = "tiny_256x128_h264.zip"
downloads = ["tiny_256x128.zip", "tiny_256x128_h264.zip"]
lengths = [3]
label_style = true
dates = "1 Jan - 2 Jan 2026"
per = { unit = "day", count = 2 }
sources = [{ label = "Test source", url = "https://example.com/source" }]
[[datasets]]
slug = "second"
release = "data-1"
preview = "second_256x128_h264.zip"
downloads = ["second_256x128.zip"]
[[datasets]]
slug = "still"
release = "data-1"
preview = "still_256x128.zip"
downloads = ["still_256x128.zip"]
'''


HELP_FRAME = """
[help]
bar = "./site/help-bar.html"
title = { site = "SOS Forge", section = "Help" }
layout = "desk"
ask = { heading = "Ask about sos-forge", suggestions = 3 }
"""


def deployed_csp(ask_origin: str = "", help_pages: bool = False) -> str:
    """Return the Content-Security-Policy CloudFront sends on site pages, or on /help/ (inline scripts allowed), from infra/lib/csp.mjs through node. Helper; not itself tested."""
    args = json.dumps({"askOrigin": ask_origin, "inlineScripts": help_pages})
    js = f"import({json.dumps((site.REPO_ROOT / 'infra' / 'lib' / 'csp.mjs').as_uri())}).then(m => process.stdout.write(m.csp({args})))"
    return subprocess.run(["node", "-e", js], capture_output=True, text=True, check=True).stdout


def build_fixture(tmp, config, **build):
    """Build the fixture site (two tiny movies, one with an extra length, the labels style and a source, and a still) from a config. Helper; not itself tested."""
    dist = tmp / "dist"
    make_dataset(dist / "tiny_h264", name="Tiny (H.264)")
    with (dist / "tiny_h264" / "playlist.sos").open("a") as f:
        f.write("lastdwell = 3000\n")   # hold the last frame, as SOS does, so the caption's end state lasts long enough to see
    make_dataset(dist / "second_h264", name="Second (H.264)")
    (dist / "still").mkdir()
    cv2.imwrite(str(dist / "still" / "still_256x128.jpg"), np.full((128, 256, 3), 90, np.uint8))
    (dist / "still" / "playlist.sos").write_text("name = Still\ndescription = {{ A still. }}\ndata = still_256x128.jpg\n")
    (tmp / "site.toml").write_text(config)
    site.build_site(tmp / "site.toml", tmp / "site", dist=dist, **build)
    return tmp / "site"


def serve(root, ask_origin=""):
    """Serve a built site as CloudFront does, with the site CSP everywhere and the help one under /help/, returning (origin, server). Helper; not itself tested."""
    policies = {False: deployed_csp(ask_origin), True: deployed_csp(ask_origin, help_pages=True)}

    class Handler(http.server.SimpleHTTPRequestHandler):
        """Serve the built site with CloudFront's CSPs and no request logging. Fixture helper; not itself tested."""

        def end_headers(self):
            """Add the CSP CloudFront sends for this path. Fixture helper; not itself tested."""
            self.send_header("Content-Security-Policy", policies[self.path.startswith("/help/")])
            super().end_headers()

        def log_message(self, *args):
            """Keep test output quiet. Fixture helper; not itself tested."""

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Handler, directory=str(root)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{server.server_port}", server


@pytest.fixture(scope="module")
def origin(tmp_path_factory):
    """Serve the fixture site built without Ask & Answer, and yield its origin. Fixture; not itself tested."""
    url, server = serve(build_fixture(tmp_path_factory.mktemp("browser"), CONFIG))
    yield url
    server.shutdown()


@pytest.fixture(scope="module")
def origin_ask(tmp_path_factory):
    """Serve the fixture site built with the real pinned Ask & Answer release, fetched from site.toml's API and checked against ask.lock.json, and yield (origin, API base); skipped when the API can't be reached, unless SOSFORGE_BROWSER_TESTS=1. Fixture; not itself tested."""
    base = site.load_config(site.REPO_ROOT / "site" / "site.toml")["ask_base"].rstrip("/")
    try:
        root = build_fixture(tmp_path_factory.mktemp("browser_ask"), CONFIG + HELP_FRAME, ask_base=base)
    except Exception as exc:
        if REQUIRED:
            raise
        pytest.skip(f"the pinned Ask & Answer release could not be fetched from {base}: {exc}")
    url, server = serve(root, base)
    yield url, base
    server.shutdown()


@pytest.fixture(scope="module")
def browser():
    """Launch Google Chrome headless through Playwright, skipping (or failing in CI) when it isn't available. Fixture; not itself tested."""
    with sync_playwright() as p:
        try:
            b = p.chromium.launch(channel="chrome", headless=True)
        except Exception as exc:
            if REQUIRED:
                raise
            pytest.skip(f"Google Chrome is not available to Playwright: {exc}")
        yield b
        b.close()


def open_page(browser, origin, path):
    """Open a page at desktop size, watching it for problems from the first request. Helper; not itself tested."""
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    problems = watch(page, origin)
    page.goto(origin + path, wait_until="networkidle")
    return page, problems


def test_watch_catches_errors(browser, origin):
    """Checks the problem watcher records a script error, a CSP violation and a missing same-origin file, so a clean page means clean. Covers tools/smoke.py watch; this test is not itself tested."""
    page, problems = open_page(browser, origin, "/404.html")
    page.evaluate("""() => { setTimeout(() => { throw new Error('boom'); });
        const s = document.createElement('script'); s.src = 'https://example.com/x.js'; document.body.append(s);
        fetch('/missing.json').catch(() => {}); }""")
    page.wait_for_timeout(800)
    text = "\n".join(problems)
    assert "boom" in text and "Content Security Policy" in text and "HTTP 404" in text and "missing.json" in text


@pytest.mark.parametrize("path", ["/", "/simulator.html", "/simulator.html?d=tiny", "/404.html"])
def test_pages_load_clean(browser, origin, path):
    """Checks each page loads under the deployed CSP with no console or script errors and no failed same-origin requests. Covers the static pages, gallery.js, simulator.js and their imports; this test is not itself tested."""
    page, problems = open_page(browser, origin, path)
    page.wait_for_timeout(1200)
    assert problems == []
    if path == "/":
        hrefs = page.eval_on_selector_all('a', 'as => as.map(a => a.getAttribute("href"))')
        assert not any(h in ("help/", "/help/", "ask.html") for h in hrefs)
        assert sum("blob/main/docs/getting-started.md" in h for h in hrefs) == 2


def test_gallery_cards_set_downloads(browser, origin):
    """Checks the cards show dates, pace chips, the total, text styles and the Data line, that clicks and arrow keys set Download's link and wording, and that a single-length movie and a still keep their pills. Covers gallery.js card, chips and the pace wording; this test is not itself tested."""
    page, problems = open_page(browser, origin, "/")
    cards = page.locator(".card")
    assert cards.count() == 3
    tiny, second, still = cards.nth(0), cards.nth(1), cards.nth(2)
    dl = tiny.locator(".actions a.btn:not(.primary)")
    assert tiny.locator(".badge.dates").inner_text() == "1 Jan - 2 Jan 2026"
    assert tiny.locator(".pills .lengths span").all_inner_texts() == ["0.5 s/day", "1.5 s/day"]
    assert tiny.locator(".pace").inner_text() == "1 s in all"
    assert tiny.locator(".actions .lengths span").all_inner_texts() == ["Painted", "SOS labels"]
    assert dl.inner_text() == "Download 1 s" and dl.get_attribute("href").endswith("/data-1/tiny_256x128.zip")
    tiny.locator("label", has_text="1.5 s/day").click()
    assert dl.inner_text() == "Download 3 s" and dl.get_attribute("href").endswith("/tiny_3s_256x128.zip")
    tiny.locator("label", has_text="SOS labels").click()
    assert dl.inner_text() == "Download 3 s, labels" and dl.get_attribute("href").endswith("/tiny_3s_labels_256x128.zip")
    tiny.locator('.pills .lengths input:checked').press("ArrowLeft")
    assert dl.inner_text() == "Download 1 s, labels" and dl.get_attribute("href").endswith("/tiny_labels_256x128.zip")
    assert tiny.locator(".source").inner_text() == "Data: Test source"
    assert tiny.locator(".source a").get_attribute("href") == "https://example.com/source"
    assert second.locator(".pills .badge").all_inner_texts() == ["1 s loop"] and second.locator(".lengths").count() == 0
    assert still.locator(".pills .badge").all_inner_texts() == ["Still image"] and still.locator(".play").count() == 0
    assert problems == []


def test_flat_preview_plays_with_captions(browser, origin):
    """Checks the play button swaps in a playing preview with its labels as captions pinned to the top, and starting another preview pauses the first. Covers gallery.js playFlat and addCaptions and captions.js in a real browser; this test is not itself tested."""
    page, problems = open_page(browser, origin, "/")
    cards = page.locator(".card")
    cards.nth(0).locator(".play").click()
    first = cards.nth(0).locator("video")
    first.evaluate("v => new Promise(r => { const t = () => v.currentTime > 0.3 ? r() : setTimeout(t, 50); t(); })")
    track = first.evaluate("v => { const t = v.textTracks[0]; return { mode: t.mode, cues: t.cues.length, line: t.cues[0].line,"
                           " text: t.cues[0].text, active: [...t.activeCues].map(c => c.text) }; }")
    assert track["mode"] == "showing" and track["cues"] == 30 and track["line"] == 0 and track["text"] == "Frame 0"
    assert track["active"] and track["active"][0].startswith("Frame ")
    cards.nth(1).locator(".play").click()
    cards.nth(1).locator("video").evaluate("v => new Promise(r => { const t = () => v.currentTime > 0.2 ? r() : setTimeout(t, 50); t(); })")
    assert first.evaluate("v => v.paused")
    assert problems == []


def test_simulator_caption_stays_put(browser, origin):
    """Checks the simulator shows the dataset's labels as a caption that follows the frames, keeps its place (centre and top; its width follows the text) while the globe is dragged, and still shows the last frame's label at the end of the movie. Covers simulator.js showCaption against a real WebGL sphere; this test is not itself tested."""
    page, problems = open_page(browser, origin, "/simulator.html?d=tiny")
    caption = page.locator("#caption")
    page.wait_for_function("() => document.getElementById('caption').textContent.startsWith('Frame ')", timeout=15000)
    first = caption.inner_text()
    centre = lambda b: (round(b["x"] + b["width"] / 2), round(b["y"]))
    box = centre(caption.bounding_box())
    canvas = page.locator(".stage canvas").bounding_box()
    cx, cy = canvas["x"] + canvas["width"] / 2, canvas["y"] + canvas["height"] / 2
    page.mouse.move(cx, cy)
    page.mouse.down()
    for step in range(1, 11):
        page.mouse.move(cx + 18 * step, cy + 6 * step)
        page.wait_for_timeout(60)
    page.mouse.up()
    assert centre(caption.bounding_box()) == box and caption.is_visible()
    # Wait for the frames to move on rather than sampling during the drag: CI's software WebGL can be slow to start.
    page.wait_for_function("t => document.getElementById('caption').textContent !== t", arg=first, timeout=15000)
    assert caption.inner_text().startswith("Frame ")
    # The end of the movie, where SOS holds for lastdwell: the caption must keep the last frame's label, not go blank.
    # A MutationObserver records every blank moment, however brief, instead of polling for one.
    page.evaluate("""() => { window.blanks = []; const c = document.getElementById('caption');
        new MutationObserver(() => { if (c.hidden || !c.textContent.trim()) window.blanks.push(c.textContent); })
          .observe(c, { attributes: true, attributeFilter: ['hidden'], childList: true, characterData: true, subtree: true });
        const s = document.getElementById('scrub'); s.value = s.max; s.dispatchEvent(new Event('input')); }""")
    page.wait_for_timeout(1500)
    assert page.evaluate("() => window.blanks") == []
    assert problems == []


def test_download_urls_cover_every_choice(origin):
    """Checks the smoke check's download list covers each dataset's own downloads and every length in both text styles. Covers tools/smoke.py download_urls; this test is not itself tested."""
    import urllib.request
    with urllib.request.urlopen(origin + "/datasets.json") as r:
        names = [u.rsplit("/", 1)[1] for u in download_urls(json.load(r))]
    assert names == sorted(["second_256x128.zip", "still_256x128.zip", "tiny_256x128.zip", "tiny_256x128_h264.zip",
                            "tiny_3s_256x128.zip", "tiny_3s_labels_256x128.zip", "tiny_labels_256x128.zip"])


def test_home_ask_box_hands_the_question_to_help(browser, origin_ask, origin):
    """Checks the home page shows Ask & Answer's box beside "Read the help" with no errors, that asking takes the reader to /help/#ask=<question>, that Help takes the question out of the address and shows the desk layout's panel, and that a build without Ask & Answer has no box. Covers js/ask.js, the pinned box() and the help layout under the deployed CSPs; the API refuses localhost, so no question is really asked."""
    url, _ = origin_ask
    page, problems = open_page(browser, url, "/")
    box = page.locator("#ask-box.ask-box")
    box.wait_for(state="visible")
    assert box.locator("input").count() == 1 and box.locator("button").inner_text() == "Ask"
    assert page.locator('link[href="ask/ask-panel.css"]').count() == 1
    assert problems == []
    seen = []
    page.on("framenavigated", lambda f: seen.append(f.url) if f == page.main_frame else None)
    box.locator("input").fill("How do I make a movie longer?")
    box.locator("button").click()
    page.wait_for_url("**/help/**")
    page.wait_for_function("() => !location.hash", timeout=10000)
    assert any(u.endswith("/help/#ask=How%20do%20I%20make%20a%20movie%20longer%3F") for u in seen), seen
    assert page.locator(".ask-panel").count() >= 1
    plain, _ = open_page(browser, origin, "/")
    assert plain.locator("#ask-box").count() == 0 and plain.locator('link[href="ask/ask-panel.css"]').count() == 0
