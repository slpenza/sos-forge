"""Headless Google Chrome tests of a built site, served with the CSP CloudFront sends (design/v0/v0.4.1.md).

They need Playwright and Google Chrome (pip install -e ".[browser]"; CI also runs playwright install chrome). Without
them the module is skipped, unless SOSFORGE_BROWSER_TESTS=1, as in CI, makes a missing browser a failure.
"""
from __future__ import annotations

import functools
import http.server
import json
import os
import re
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
from smoke import download_urls, preview_paths, watch  # noqa: E402

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
noaa_catalog = "https://sos.noaa.gov/catalog/datasets/second/"
[[datasets]]
slug = "still"
release = "data-1"
preview = "still_256x128.zip"
downloads = ["still_256x128.zip"]
'''


CHANGES = [
    {"version": "v0.2.0", "date": "2026-11-24", "notes": "https://example.org/v0.2.0",
     "entries": [{"kind": "New dataset", "text": "Tiny joins the gallery."}, {"kind": "Simulator", "text": "Labels on the sphere."}]},
    {"version": "v0.1.0", "date": "2026-11-02", "notes": "https://example.org/v0.1.0",
     "entries": [{"kind": "Gallery", "text": "Cards play their previews."}, {"kind": "Help", "text": "Ask from home."}]},
]


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
    """Build the fixture site (two tiny movies, one with an extra length, the labels style, a version for each with its own labels, and a source, and a still) from a config. Helper; not itself tested."""
    dist = tmp / "dist"
    make_dataset(dist / "tiny_h264", name="Tiny (H.264)")
    for folder, word in (("tiny_3s", "Three"), ("tiny_labels", "Label"), ("tiny_3s_labels", "Both")):
        make_dataset(dist / folder, codec="mpeg4", name=folder)
        (dist / folder / "labels.txt").write_text("".join(f"{word} {i}\n" for i in range(30)))
    with (dist / "tiny_h264" / "playlist.sos").open("a") as f:
        f.write("lastdwell = 3000\n")   # hold the last frame, as SOS does, so the label's end state lasts long enough to see
    make_dataset(dist / "second_h264", name="Second (H.264)")
    (dist / "still").mkdir()
    cv2.imwrite(str(dist / "still" / "still_256x128.jpg"), np.full((128, 256, 3), 90, np.uint8))
    (dist / "still" / "playlist.sos").write_text("name = Still\ndescription = {{ A still. }}\ndata = still_256x128.jpg\n")
    (tmp / "site.toml").write_text(config)
    site.build_site(tmp / "site.toml", tmp / "site", dist=dist, **build)
    # What's new comes from the repo's real tags, which CI's shallow checkout lacks: the fixture's list is fixed.
    (tmp / "site" / "changes.json").write_text(json.dumps({"versions": CHANGES}))
    return tmp / "site"


def serve(root, ask_origin=""):
    """Serve a built site as CloudFront does, with the site CSP everywhere and the help one under /help/ and byte ranges so videos can seek, returning (origin, server). Helper; not itself tested."""
    policies = {False: deployed_csp(ask_origin), True: deployed_csp(ask_origin, help_pages=True)}

    class Handler(http.server.SimpleHTTPRequestHandler):
        """Serve the built site with CloudFront's CSPs and no request logging. Fixture helper; not itself tested."""

        def end_headers(self):
            """Add the CSP CloudFront sends for this path, and say byte ranges are served. Fixture helper; not itself tested."""
            self.send_header("Content-Security-Policy", policies[self.path.startswith("/help/")])
            self.send_header("Accept-Ranges", "bytes")
            super().end_headers()

        def do_GET(self):
            """Serve the asked byte range as a 206, as CloudFront does (without it Chrome can't seek a video and jumps back to the start), or else the whole file. Fixture helper; not itself tested."""
            rng = re.fullmatch(r"bytes=(\d+)-(\d*)", self.headers.get("Range", ""))
            path = Path(self.translate_path(self.path))
            if not rng or not path.is_file():
                return super().do_GET()
            data = path.read_bytes()
            start, end = int(rng[1]), min(int(rng[2]) if rng[2] else len(data) - 1, len(data) - 1)
            self.send_response(206)
            self.send_header("Content-Type", self.guess_type(str(path)))
            self.send_header("Content-Range", f"bytes {start}-{end}/{len(data)}")
            self.send_header("Content-Length", str(end - start + 1))
            self.end_headers()
            self.wfile.write(data[start:end + 1])

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


@pytest.mark.parametrize("path", ["/", "/simulator.html", "/simulator.html?d=tiny", "/changes.html", "/404.html"])
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
    """Checks the cards show date and length pills and the Data line, that the Length and Text selects set what Open in simulator opens and what Download fetches, and that a single-length movie and a still keep a plain Download link and no selects, and that only a dataset with a NOAA catalog link shows it. Covers gallery.js card, versions.js versionPicker and the pace wording; this test is not itself tested."""
    page, problems = open_page(browser, origin, "/")
    cards = page.locator(".card")
    assert cards.count() == 3
    tiny, second, still = cards.nth(0), cards.nth(1), cards.nth(2)
    assert tiny.locator(".pills .badge").all_inner_texts() == ["1 Jan - 2 Jan 2026"]
    assert tiny.locator(".source").inner_text() == "Data: Test source"
    assert tiny.locator(".source a").get_attribute("href") == "https://example.com/source"
    length, text = tiny.get_by_label("Length"), tiny.get_by_label("Text")
    sim, dl = tiny.locator(".actions a.primary"), tiny.locator(".actions a.btn:not(.primary)")
    assert length.locator("option").all_inner_texts() == ["1 s · 0.5 s/day", "3 s · 1.5 s/day"]
    assert text.locator("option").all_inner_texts() == ["Painted into the picture", "SOS labels, fixed on the sphere"]
    state = lambda: (sim.get_attribute("href"), dl.get_attribute("href").rsplit("/", 1)[1], dl.inner_text())
    assert state() == ("simulator.html?d=tiny", "tiny_256x128.zip", "Download")
    assert tiny.locator(".poster a").get_attribute("href") == "simulator.html?d=tiny"
    length.select_option("3")
    assert state() == ("simulator.html?d=tiny&s=3", "tiny_3s_256x128.zip", "Download")
    text.select_option("labels")
    assert state() == ("simulator.html?d=tiny&s=3&text=labels", "tiny_3s_labels_256x128.zip", "Download")
    assert tiny.locator(".poster a").get_attribute("href") == "simulator.html?d=tiny&s=3&text=labels"
    length.select_option("1")
    assert state() == ("simulator.html?d=tiny&text=labels", "tiny_labels_256x128.zip", "Download")
    assert tiny.locator(".catalog").count() == 0 and still.locator(".catalog").count() == 0
    assert second.locator(".catalog a").inner_text() == "In NOAA's SOS Data Catalog"
    assert second.locator(".catalog a").get_attribute("href") == "https://sos.noaa.gov/catalog/datasets/second/"
    for c, pills in ((second, ["1 s loop"]), (still, ["Still image"])):
        assert c.locator(".pills .badge").all_inner_texts() == pills and c.locator("select").count() == 0
        assert c.locator(".actions a.btn:not(.primary)").inner_text() == "Download"
    assert still.locator(".play").count() == 0
    assert problems == []


def test_flat_preview_plays_with_captions(browser, origin):
    """Checks the play button swaps in a playing preview with its labels as captions pinned to the top, that changing the version while it plays swaps in that version's preview and labels, and starting another preview pauses the first. Covers gallery.js playFlat and addCaptions and captions.js in a real browser; this test is not itself tested."""
    page, problems = open_page(browser, origin, "/")
    cards = page.locator(".card")
    cards.nth(0).locator(".play").click()
    first = cards.nth(0).locator("video")
    first.evaluate("v => new Promise(r => { const t = () => v.currentTime > 0.3 ? r() : setTimeout(t, 50); t(); })")
    track = first.evaluate("v => { const t = v.textTracks[0]; return { mode: t.mode, cues: t.cues.length, line: t.cues[0].line,"
                           " text: t.cues[0].text, active: [...t.activeCues].map(c => c.text) }; }")
    assert track["mode"] == "showing" and track["cues"] == 30 and track["line"] == 0 and track["text"] == "Frame 0"
    assert track["active"] and track["active"][0].startswith("Frame ")
    cards.nth(0).get_by_label("Length").select_option("3")
    cards.nth(0).get_by_label("Text").select_option("labels")
    swapped = cards.nth(0).locator("video")
    swapped.evaluate("v => new Promise(r => { const t = () => v.currentTime > 0.1 && v.textTracks[0]?.cues?.length ? r() : setTimeout(t, 50); t(); })")
    assert swapped.evaluate("v => [new URL(v.src).pathname, v.textTracks[0].cues[0].text]") == ["/datasets/tiny/3s_labels/preview.mp4", "Both 0"]
    first = swapped
    cards.nth(1).locator(".play").click()
    cards.nth(1).locator("video").evaluate("v => new Promise(r => { const t = () => v.currentTime > 0.2 ? r() : setTimeout(t, 50); t(); })")
    assert first.evaluate("v => v.paused")
    assert problems == []


def canvas_pixels(page):
    """Return the simulator's sphere canvas as it looks now, as a BGR array, with the controls over it masked out. Helper for the label tests; not itself tested."""
    page.wait_for_timeout(400)   # let a few animation frames draw what was just changed
    png = page.locator("#globe").screenshot(mask=[page.locator(".controls"), page.locator("#status")])
    return cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)


def label_box(page):
    """Return the bounding box (x0, y0, x1, y1) of what the Labels button adds to the paused sphere, by drawing it with labels on and off, and leave labels on. Helper for test_simulator_labels_on_the_sphere; not itself tested."""
    toggle = page.locator("#labels-toggle")
    assert toggle.get_attribute("aria-pressed") == "true"
    on = canvas_pixels(page)
    toggle.click()
    off = canvas_pixels(page)
    toggle.click()
    ys, xs = np.nonzero(np.abs(on.astype(int) - off.astype(int)).max(axis=2) > 40)
    assert len(xs) > 20, "the labels drew nothing"
    return (xs.min(), ys.min(), xs.max(), ys.max()), off


def test_simulator_labels_on_the_sphere(browser, origin):
    """Checks the SOS-labels version starts with its labels on and the painted one with them off, as their playlists say; that the label is drawn on the sphere's front below the equator, follows the frames, stays put while the picture spins, moves with the sphere when the view is dragged, and still shows the last frame's line at the end of the movie. Covers simulator.js showLabel and setLabels, Globe.setLabel and the label shader against a real WebGL sphere; this test is not itself tested."""
    page, problems = open_page(browser, origin, "/simulator.html?d=tiny")
    toggle, text = page.locator("#labels-toggle"), page.locator("#label-text")
    page.wait_for_function("() => document.getElementById('time').textContent !== '0:00 / 0:00'", timeout=15000)
    assert toggle.get_attribute("aria-pressed") == "false" and text.text_content() == ""
    toggle.click()
    page.wait_for_function("() => document.getElementById('label-text').textContent.startsWith('Frame ')", timeout=15000)
    page.goto(origin + "/simulator.html?d=tiny&text=labels", wait_until="networkidle")
    page.wait_for_function("() => document.getElementById('label-text').textContent.startsWith('Label ')", timeout=15000)
    first = text.text_content()
    page.wait_for_function("t => document.getElementById('label-text').textContent !== t", arg=first, timeout=15000)
    page.locator("#play").click()                       # pause, so only the labels and the spin change the picture
    (x0, y0, x1, y1), off = label_box(page)
    h, w = off.shape[:2]
    assert abs((x0 + x1) / 2 - w / 2) < w * 0.05 and (y0 + y1) / 2 > h / 2 and x1 - x0 > y1 - y0
    # Spin turns the picture about 15 degrees, which would carry a label drawn on it about 70 px. Glyph edges over a
    # bright background add less to the on/off difference, so the box is compared by its centre, within a few pixels.
    centre = lambda b: ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)
    page.locator("#spin").click()
    page.wait_for_timeout(3000)
    page.locator("#spin").click()
    box, turned = label_box(page)
    assert np.abs(turned.astype(int) - off.astype(int)).mean() > 2, "the picture didn't turn"
    assert np.hypot(*np.subtract(centre(box), centre((x0, y0, x1, y1)))) < 8, "the label moved with the picture"
    canvas = page.locator("#globe").bounding_box()
    cx, cy = canvas["x"] + canvas["width"] / 2, canvas["y"] + canvas["height"] / 2
    page.mouse.move(cx, cy)
    page.mouse.down()
    for step in range(1, 11):                          # 30 px is 9 degrees, so the next copy stays beyond the limb
        page.mouse.move(cx + 3 * step, cy)
        page.wait_for_timeout(40)
    page.mouse.up()
    dragged, _ = label_box(page)
    assert centre(dragged)[0] > centre(box)[0] + 15, "the label didn't move with the sphere"
    # The end of the movie, where SOS holds for lastdwell: the label must keep the last frame's line, not go blank.
    # A MutationObserver records every blank moment, however brief, instead of polling for one.
    page.locator("#play").click()
    page.evaluate("""() => { window.blanks = []; const c = document.getElementById('label-text');
        new MutationObserver(() => { if (!c.textContent.trim()) window.blanks.push(c.textContent); })
          .observe(c, { childList: true, characterData: true, subtree: true });
        const s = document.getElementById('scrub'); s.value = s.max; s.dispatchEvent(new Event('input')); }""")
    page.wait_for_timeout(1500)
    assert page.evaluate("() => window.blanks") == [] and text.text_content().startswith("Label ")
    assert problems == []


def test_simulator_switches_versions(browser, origin):
    """Checks the simulator opens the version a card link names, shows the same Length and Text selects, and switching plays that version's preview and labels from the same place, with labels on for the SOS-labels version, recording it in the address; a dataset without versions hides the selects. Covers simulator.js playVersion and loadGallery with versions.js in a real browser; this test is not itself tested."""
    page, problems = open_page(browser, origin, "/simulator.html?d=tiny&s=3")
    toggle = page.locator("#labels-toggle")
    page.wait_for_function("() => !document.getElementById('labels-toggle').disabled", timeout=15000)
    toggle.click()                                      # the painted version ships its labels hidden
    page.wait_for_function("() => document.getElementById('label-text').textContent.startsWith('Three ')", timeout=15000)
    length, text = page.locator("#versions").get_by_label("Length"), page.locator("#versions").get_by_label("Text")
    assert length.input_value() == "3" and text.input_value() == "painted"
    assert page.locator("#ds-downloads a").all_inner_texts() == ["tiny_3s_256x128.zip"]
    page.locator("#play").click()                       # pause, so the place to keep is fixed
    page.evaluate("() => { const s = document.getElementById('scrub'); s.value = String(s.max * 0.5); s.dispatchEvent(new Event('input')); }")
    page.wait_for_function("() => document.getElementById('label-text').textContent === 'Three 15'", timeout=15000)
    text.select_option("labels")
    page.wait_for_function("() => document.getElementById('label-text').textContent === 'Both 15'", timeout=15000)
    assert page.evaluate("() => location.search") == "?d=tiny&s=3&text=labels"
    assert page.locator("#play").inner_text() == "Play" and toggle.get_attribute("aria-pressed") == "true"
    assert page.locator("#ds-downloads a").all_inner_texts() == ["tiny_3s_labels_256x128.zip"]
    page.locator("#dataset").select_option("second")
    page.wait_for_function("() => location.search === '?d=second'")
    assert page.locator("#versions").is_hidden()
    assert problems == []


def test_whats_new_lists_visitor_changes(browser, origin):
    """Checks the What's new page lists every version newest first with its date, its number linking to its release notes and its lines tagged by kind; the home page shows the latest three lines, dated, with a link to the page; every site page's nav links What's new; and a phone-width page doesn't scroll sideways. Covers changes.js renderAll, renderLatest and init against the built site; the list's source is tested by test_changes.py."""
    page, problems = open_page(browser, origin, "/changes.html")
    page.wait_for_selector("#changes .change")
    heads = page.locator("#changes .change h2")
    assert [" ".join(h.split()) for h in heads.all_inner_texts()] == ["24 Nov 2026 v0.2.0", "2 Nov 2026 v0.1.0"]
    assert heads.first.locator("a.ver").get_attribute("href") == "https://example.org/v0.2.0"
    assert page.locator("#changes .change").first.locator("li").all_inner_texts() == [
        "New dataset Tiny joins the gallery.", "Simulator Labels on the sphere."]
    assert page.locator('nav a[aria-current="page"]').inner_text() == "What's new"
    page.goto(origin + "/", wait_until="networkidle")
    home = page.locator("#whats-new")
    home.wait_for(state="visible")
    assert home.locator("li").all_inner_texts() == ["24 Nov 2026 New dataset Tiny joins the gallery.",
                                                    "24 Nov 2026 Simulator Labels on the sphere.",
                                                    "2 Nov 2026 Gallery Cards play their previews."]
    assert home.locator("a").get_attribute("href") == "changes.html"
    for path in ("/", "/simulator.html"):
        page.goto(origin + path, wait_until="networkidle")
        assert page.locator('nav a[href="changes.html"]').inner_text() == "What's new"
    phone = browser.new_page(viewport={"width": 390, "height": 844})
    phone.goto(origin + "/changes.html", wait_until="networkidle")
    phone.wait_for_selector("#changes .change")
    assert phone.evaluate("document.documentElement.scrollWidth") <= 390
    phone.close()
    assert problems == []


def test_download_urls_cover_every_choice(origin):
    """Checks the smoke check's lists cover each dataset's own downloads, every length in both text styles, and every version's preview and labels, all of which the built site serves. Covers tools/smoke.py download_urls and preview_paths; this test is not itself tested."""
    import urllib.request
    with urllib.request.urlopen(origin + "/datasets.json") as r:
        manifest = json.load(r)
    names = [u.rsplit("/", 1)[1] for u in download_urls(manifest)]
    assert names == sorted(["second_256x128.zip", "still_256x128.zip", "tiny_256x128.zip", "tiny_256x128_h264.zip",
                            "tiny_3s_256x128.zip", "tiny_3s_labels_256x128.zip", "tiny_labels_256x128.zip"])
    paths = preview_paths(manifest)
    assert paths == sorted([*(f"datasets/tiny/{v}{f}" for v in ("", "3s/", "labels/", "3s_labels/") for f in ("labels.txt", "preview.mp4")),
                            "datasets/second/labels.txt", "datasets/second/preview.mp4"])
    for p in paths:
        with urllib.request.urlopen(f"{origin}/{p}") as r:
            assert r.status == 200


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
