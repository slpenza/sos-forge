#!/usr/bin/env python3
"""Smoke-check a deployed sos-forge site in headless Google Chrome (design/v0/v0.4.1.md).

Run: python tools/smoke.py https://sos.stevenpenza.com

Every page must load with no console errors, script errors, CSP violations or failed same-origin requests. Every
download on every gallery card (each length, each text style) must answer, and so must every version's preview and
labels on the site. The release workflow runs this after
each deploy; tests/test_browser.py reuses watch() against a local build.
"""
from __future__ import annotations

import json
import sys
import urllib.request

PAGES = ("/", "/simulator.html", "/help/", "/changes.html", "/404.html")


def watch(page, origin: str) -> list[str]:
    """Record a page's console errors, script errors and failed or 4xx/5xx same-origin requests into a list that fills as the page runs. Tested by tests/test_browser.py::test_watch_catches_errors and every page test there."""
    problems: list[str] = []
    page.on("console", lambda m: problems.append(f"console {m.type}: {m.text}") if m.type == "error" else None)
    page.on("pageerror", lambda e: problems.append(f"script error: {e}"))
    page.on("response", lambda r: problems.append(f"HTTP {r.status} {r.url}")
            if r.status >= 400 and r.url.startswith(origin) else None)
    page.on("requestfailed", lambda r: problems.append(f"failed {r.url}: {r.failure}")
            if r.url.startswith(origin) and "ERR_ABORTED" not in str(r.failure) else None)
    return problems


def download_urls(manifest: dict) -> list[str]:
    """Return every download a gallery offers: each dataset's own, and each length's in both text styles. Tested by tests/test_browser.py::test_download_urls_cover_every_choice."""
    urls = []
    for d in manifest.get("datasets", []):
        urls += [x["url"] for x in d.get("downloads", [])]
        for length in d.get("lengths", []):
            urls += [x["url"] for x in length.get("downloads", [])]
            if length.get("labels"):
                urls.append(length["labels"]["url"])
    return sorted(set(urls))


def preview_paths(manifest: dict) -> list[str]:
    """Return the site paths of every version's browser preview and labels, the files the cards and the simulator play. Tested by tests/test_browser.py::test_download_urls_cover_every_choice."""
    paths = []
    for d in manifest.get("datasets", []):
        for length in d.get("lengths", []):
            for p in (length.get("previews") or {}).values():
                paths += [x for x in (p.get("video"), p.get("labels")) if x]
    return sorted(set(paths))


def answers(url: str) -> bool:
    """Return whether a download answers with its first byte, following redirects. Exercised by the release workflow against GitHub; not unit tested."""
    req = urllib.request.Request(url, headers={"Range": "bytes=0-0", "User-Agent": "sos-forge-smoke"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status in (200, 206)
    except Exception:
        return False


def main(argv: list[str]) -> int:
    """Load each page and check every download and version preview of a deployed site, printing what failed and returning non-zero if anything did. Exercised by the release workflow after each deploy; not unit tested."""
    from playwright.sync_api import sync_playwright
    if len(argv) != 2:
        print(__doc__)
        return 2
    origin = argv[1].rstrip("/")
    failures = []
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        for path in PAGES:
            page = browser.new_page()
            problems = watch(page, origin)
            page.goto(origin + path, wait_until="networkidle")
            page.wait_for_timeout(1500)
            failures += [f"{path}: {x}" for x in problems]
            page.close()
        browser.close()
    with urllib.request.urlopen(origin + "/datasets.json", timeout=60) as r:
        manifest = json.load(r)
    urls, previews = download_urls(manifest), preview_paths(manifest)
    failures += [f"download does not answer: {u}" for u in urls if not answers(u)]
    failures += [f"preview does not answer: {p}" for p in previews if not answers(f"{origin}/{p}")]
    for f in failures:
        print("FAIL", f)
    print(f"{len(PAGES)} pages, {len(urls)} downloads, {len(previews)} preview files: "
          f"{'all good' if not failures else f'{len(failures)} problems'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
