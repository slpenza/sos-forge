"""Tests for the sos.stevenpenza.com site builder: playlists, bundles, previews, release downloads and assembly."""
from __future__ import annotations

import io
import json
import functools
import http.server
import subprocess
import threading
import zipfile
from pathlib import Path

import pytest

from sosforge import site
from sosforge.encode import find_ffmpeg
from sosforge.recipe import RecipeError

README = """# T

## Credits

- NASA Black Marble, via NASA GIBS
- © OpenStreetMap contributors

## Licences

- Contains OpenStreetMap data (ODbL 1.0).

## Settings
"""


def make_dataset(folder: Path, codec: str = "libx264", name: str = "Tiny") -> Path:
    """Write a dataset folder with a 1-second 256x128 video, its playlist and a README. Test helper; not itself tested."""
    folder.mkdir(parents=True, exist_ok=True)
    stem = folder.name
    data = f"{stem}_256x128.mp4"
    subprocess.run([find_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc=s=256x128:d=1:r=30", "-c:v", codec, "-pix_fmt", "yuv420p", str(folder / data)], check=True)
    (folder / "playlist.sos").write_text(f"name = {name}\ndescription = {{{{ A test. Credits: X. }}}}\ndata = {data}\nfps = 30\n")
    (folder / "README.md").write_text(README)
    (folder / "labels.txt").write_text("".join(f"Frame {i}\n" for i in range(30)))
    return folder


def test_parse_playlist_and_readme():
    """Checks playlist values parse to numbers and joined descriptions, and README credit and licence bullets are read. Covers parse_playlist and readme_lists; this test is not itself tested."""
    p = site.parse_playlist("name = Moon\ndescription = {{ One\n two }}\nfps = 30\ntiltx = 6.68\n# x\nnope\n")
    assert p == {"name": "Moon", "description": "One two", "fps": 30, "tiltx": 6.68}
    lists = site.readme_lists(README + "\n## Credits\n\n- (none declared)\n")
    assert lists["credits"] == ["NASA Black Marble, via NASA GIBS", "© OpenStreetMap contributors"]
    assert lists["licences"] == ["Contains OpenStreetMap data (ODbL 1.0)."]


def test_recipe_dates():
    """Checks spans of two years or more read as years, shorter ones as day dates, and recipes without a span or folder give None. Covers date_label and recipe_dates against the repo's recipes; this test is not itself tested."""
    assert site.date_label("1820-01-01", "2022-01-01") == "1820–2022"
    assert site.date_label("2026-03-01", "2026-11-23") == "1 Mar 2026 – 23 Nov 2026"
    assert site.date_label("2026-11-23", "2026-11-23T12:00:00") == "23 Nov 2026"
    assert site.recipe_dates("getting_richer") == "1820–2022" and site.recipe_dates("cities_growing") == "1975–2030"
    assert site.recipe_dates("where_the_world_plays") is None and site.recipe_dates("no_such_recipe") is None


def test_lengths_and_download_checks(tmp_path, monkeypatch):
    """Checks lengths in site.toml are validated, named as sos bundle names them (extra lengths and the SOS-labels style as MPEG-4 only), listed shortest first with the preview's own length marked, and that a build checking downloads fails on a missing asset but only warns when a release can't be listed. Covers load_config lengths, per, label_style and sources, variant_name, variant_names, length_entries, missing_downloads, recipe_years and build_site's check with stubbed HTTP; this test is not itself tested."""
    head = '[site]\ndomain="d"\n[[datasets]]\nslug="gr"\nrelease="data-1"\npreview="gr_4k_h264.zip"\n'
    cfg = tmp_path / "s.toml"
    for body, msg in (('downloads=["gr_4k.zip"]\nlengths=[20, 20]\n', "distinct"), ('downloads=["gr_4k.zip"]\nlengths=[0]\n', "distinct"),
                      ('lengths=[20]\n', "need a release and downloads"), ('downloads=["other.zip"]\nlengths=[20]\n', "start with gr_")):
        cfg.write_text(head + body)
        with pytest.raises(RecipeError, match=msg):
            site.load_config(cfg)
    cfg.write_text(head + 'downloads=["gr_4k.zip", "gr_4k_h264.zip"]\nlengths=[120, 20]\n')
    d = site.load_config(cfg)["datasets"][0]
    assert d["lengths"] == [20, 120] and site.variant_name("gr_4k_h264.zip", "gr", 120) == "gr_120s_4k_h264.zip"
    assert site.variant_name("gr_4k.zip", "gr", None, "labels") == "gr_labels_4k.zip"
    entries = site.length_entries(d, "o/r", 40.4)
    assert [(e["seconds"], e["default"], len(e["downloads"])) for e in entries] == [(20, False, 1), (40, True, 2), (120, False, 1)]
    assert entries[2]["downloads"][0] == {"label": "gr_120s_4k.zip", "url": "https://github.com/o/r/releases/download/data-1/gr_120s_4k.zip"}
    assert all(e["labels"] is None for e in entries)
    styled = {**d, "label_style": True}
    assert [e["labels"]["label"] for e in site.length_entries(styled, "o/r", 40.4)] == [
        "gr_20s_labels_4k.zip", "gr_labels_4k.zip", "gr_120s_labels_4k.zip"]
    held = {"gr_4k.zip", "gr_4k_h264.zip", "gr_20s_4k.zip", "gr_labels_4k.zip", "gr_20s_labels_4k.zip", "gr_120s_labels_4k.zip"}
    calls = []
    monkeypatch.setattr(site, "http_request", lambda url, **kw: (calls.append(url), json.dumps({"assets": [{"name": n} for n in held]}).encode())[1])
    assert site.missing_downloads("o/r", [d, styled]) == ["data-1/gr_120s_4k.zip"] * 2 and len(calls) == 1
    with pytest.raises(RecipeError, match="gr_120s_4k.zip"):
        site.build_site(cfg, tmp_path / "out", check_downloads=True)

    def offline(url, **kw):
        """Fail like a network outage. Test helper; not itself tested."""
        raise RuntimeError("request failed")
    monkeypatch.setattr(site, "http_request", offline)
    assert site.missing_downloads("o/r", [d]) == []
    assert site.recipe_years("getting_richer") == pytest.approx(202.0, abs=0.01) and site.recipe_years("where_the_world_plays") is None
    for per in ('per = "sport"\n', 'per = { unit = "sport", count = 0 }\n', 'per = { unit = "", count = 9 }\n'):
        cfg.write_text(head + 'downloads=["gr_4k.zip"]\n' + per)
        with pytest.raises(RecipeError, match="per must be"):
            site.load_config(cfg)
    for src in ('sources = [{ label = "X", url = "http://x" }]\n', 'sources = [{ url = "https://x" }]\n', 'sources = "https://x"\n'):
        cfg.write_text(head + 'downloads=["gr_4k.zip"]\n' + src)
        with pytest.raises(RecipeError, match="sources must be"):
            site.load_config(cfg)
    cfg.write_text(head + 'downloads=["gr_4k.zip"]\nsources = [{ label = "X", url = "https://x.example" }]\n')
    assert site.load_config(cfg)["datasets"][0]["sources"] == [{"label": "X", "url": "https://x.example"}]
    cfg.write_text(head + 'downloads=["gr_4k.zip"]\nlabel_style = "yes"\n')
    with pytest.raises(RecipeError, match="true or false"):
        site.load_config(cfg)
    cfg.write_text(head + 'downloads=["gr_4k.zip"]\nper = { unit = "sport", count = 9 }\n')
    assert site.load_config(cfg)["datasets"][0]["per"] == {"unit": "sport", "count": 9}


def test_config_validation(tmp_path):
    """Checks site.toml defaults, and that duplicate slugs, unknown keys and entries without a source are refused. Covers load_config; this test is not itself tested."""
    ok = tmp_path / "ok.toml"
    ok.write_text('[site]\ndomain="d"\n[[datasets]]\nslug="a"\nrelease="v0.1.0"\npreview="a.zip"\n')
    cfg = site.load_config(ok)
    assert cfg["preview_width"] == 2048 and cfg["ask_projects"] == ["sos"] and cfg["datasets"][0]["downloads"] == []
    for body, msg in (('[[datasets]]\nslug="a"\ndist="x"\n[[datasets]]\nslug="a"\ndist="y"\n', "unique slug"),
                      ('[site]\ndomian="x"\n', "did you mean 'domain'"),
                      ('[[datasets]]\nslug="a"\nrelease="v1"\n', "give release")):
        bad = tmp_path / "bad.toml"
        bad.write_text(body)
        with pytest.raises(RecipeError, match=msg):
            site.load_config(bad)


def test_bundle_and_folder_dataset(tmp_path):
    """Checks bundles are named by size and codec, zips extract safely, a dataset becomes a preview, poster and entry, and MPEG-4 is refused for previews. Covers bundle, unzip_folder, media_info, make_preview and dataset_entry; this test is not itself tested."""
    dist = tmp_path / "dist"
    make_dataset(dist / "tiny", codec="mpeg4")
    make_dataset(dist / "tiny_h264", name="Tiny (H.264)")
    for folder, data in (("tiny", "tiny_256x128.mp4"), ("tiny_h264", "tiny_256x128_h264.mp4")):
        (dist / folder / f"{folder}_256x128.mp4").rename(dist / folder / data)
        pl = dist / folder / "playlist.sos"
        pl.write_text(pl.read_text().replace(f"data = {folder}_256x128.mp4", f"data = {data}"))
    zips = site.bundle("tiny", dist)
    assert [z.name for z in zips] == ["tiny_256x128.zip", "tiny_256x128_h264.zip"]
    folder = site.unzip_folder(zips[1], tmp_path / "x")
    assert (folder / "playlist.sos").exists() and folder.name == "tiny_h264"
    out = tmp_path / "site"
    entry = site.dataset_entry(folder, "tiny", out, 2048, find_ffmpeg(), [{"label": "z", "url": "u"}])
    assert entry["name"] == "Tiny" and entry["kind"] == "movie" and entry["width"] == 256 and entry["duration_s"] == pytest.approx(1, abs=0.1)
    assert (out / entry["preview"]).exists() and (out / entry["poster"]).exists() and entry["downloads"][0]["url"] == "u"
    assert site.media_info(out / entry["preview"], find_ffmpeg())["codec"] == "h264"
    assert entry["credits"][1] == "© OpenStreetMap contributors"
    assert entry["labels"] == "datasets/tiny/labels.txt" and (out / entry["labels"]).read_text().startswith("Frame 0\n")
    assert entry["dates"] is None
    with pytest.raises(RecipeError, match="H.264 folder"):
        site.dataset_entry(dist / "tiny", "tiny", out, 2048, find_ffmpeg(), [])
    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as z:
        z.writestr("../escape.txt", "x")
    with pytest.raises(RecipeError, match="unsafe path"):
        site.unzip_folder(evil, tmp_path / "y")
    with pytest.raises(RecipeError, match="no built folders"):
        site.bundle("missing", dist)


def test_release_assets_are_fetched_and_cached(monkeypatch):
    """Checks a release asset is found by name, fetched through the asset API with the token, downloaded from the signed address without it, served from cache, and that without a token the public download link is used; a missing asset names what exists. Covers release_asset with stubbed HTTP; real GitHub is exercised by site builds."""
    calls, redirects = [], []
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as z:
        z.writestr("d/playlist.sos", "data = x.mp4\n")

    def fake(url, headers=None, **kw):
        """Serve release metadata or the asset bytes. Test helper; not itself tested."""
        calls.append((url, dict(headers or {})))
        if "/releases/tags/" in url:
            return json.dumps({"assets": [{"name": "d.zip", "url": "https://api/assets/1",
                                           "browser_download_url": "https://dl/d.zip"}]}).encode()
        return payload.getvalue()

    monkeypatch.setenv("GH_TOKEN", "t0ken")
    monkeypatch.setattr(site, "http_request", fake)
    monkeypatch.setattr(site, "redirect_location", lambda url, headers: (redirects.append((url, headers)), "https://signed/d.zip")[1])
    p = site.release_asset("o/r", "v0.1.0", "d.zip")
    assert zipfile.is_zipfile(p) and len(calls) == 2 and calls[1][0] == "https://signed/d.zip"
    assert calls[0][1]["Authorization"] == "Bearer t0ken" and "Authorization" not in calls[1][1]
    assert redirects == [("https://api/assets/1", {"Accept": "application/octet-stream", "Authorization": "Bearer t0ken"})]
    site.release_asset("o/r", "v0.1.0", "d.zip")
    assert len(calls) == 2
    monkeypatch.delenv("GH_TOKEN")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    site.release_asset("o/r", "v0.2.0", "d.zip")
    assert calls[-1][0] == "https://dl/d.zip" and len(redirects) == 1
    with pytest.raises(RecipeError, match="has: d.zip"):
        site.release_asset("o/r", "v0.1.0", "other.zip")


STAND_IN_CORPUS = """import json, pathlib, sys, urllib.request
args = sys.argv
opt = lambda name: args[args.index(name) + 1] if name in args else None
project = pathlib.Path(opt('--project'))
if '--fetch' in args:
    lock = json.loads((project / 'ask.lock.json').read_text())
    into = pathlib.Path(opt('--into'))
    for f in lock['files']:
        if f != 'corpus.py':
            (into / f).parent.mkdir(parents=True, exist_ok=True)
            (into / f).write_bytes(urllib.request.urlopen(opt('--fetch') + '/artifacts/' + lock['release'] + '/' + f).read())
    sys.exit(0)
block = json.loads((project / 'package.json').read_text())['askAnswer']
out = pathlib.Path(opt('--docs-out'))
out.mkdir(parents=True, exist_ok=True)
(out / 'index.html').write_text(json.dumps({'key': args[args.index(str(project)) + 1], 'block': block, 'ask_base': opt('--ask-base'),
    'files': sorted(str(p.relative_to(project)) for p in project.rglob('*') if p.is_file())}))
"""


def stand_in_api(root):
    """Serve a stand-in Ask & Answer API from root: release v9.9.9's artifacts (a corpus.py that fetches the lock's files and records what it builds help from, the panel files and a docsite page), and write the matching ask.lock.json in root/project. Return (base URL, lock path, server). Helper; not itself tested."""
    files = {"corpus.py": STAND_IN_CORPUS, "docsite/index.html": "<!doctype html>",
             **{f: f"/* {f} v9.9.9 */" for f in site.PANEL_FILES}}
    for name, text in files.items():
        (root / "artifacts" / "v9.9.9" / name).parent.mkdir(parents=True, exist_ok=True)
        (root / "artifacts" / "v9.9.9" / name).write_text(text)
    (root / "project").mkdir()
    lock = root / "project" / "ask.lock.json"
    lock.write_text(json.dumps({"release": "v9.9.9", "files": {n: site.sha384(t.encode()) for n, t in files.items()}}))
    handler = functools.partial(QuietHandler, directory=str(root))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{server.server_port}", lock, server


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    """Serve files without logging each request. Test helper; not itself tested."""

    def log_message(self, *args):
        """Keep test output quiet. Test helper; not itself tested."""


def test_fetch_ask_checks_the_lock(tmp_path):
    """Checks the pinned release is fetched when corpus.py matches ask.lock.json, and refused when the served corpus.py was changed, the lock is missing, or it doesn't pin the panel. Covers ask_lock, sha384 and fetch_ask against a stand-in API; the real API is exercised by sos site."""
    base, lock, server = stand_in_api(tmp_path / "api")
    try:
        got = site.fetch_ask(base + "/", tmp_path / "fetched", lock)
        assert sorted(str(p.relative_to(got)) for p in got.rglob("*") if p.is_file()) == sorted(
            ["corpus.py", "docsite/index.html", *site.PANEL_FILES])
        assert (got / "ask-panel.js").read_text() == "/* ask-panel.js v9.9.9 */"
        served = tmp_path / "api" / "artifacts" / "v9.9.9" / "corpus.py"
        served.write_text(served.read_text() + "# changed\n")
        with pytest.raises(RecipeError, match="not the one ask.lock.json pins"):
            site.fetch_ask(base, tmp_path / "again", lock)
        with pytest.raises(RecipeError, match="not found"):
            site.ask_lock(tmp_path / "nowhere.json")
        lock.write_text(json.dumps({"release": "v9.9.9", "files": {"corpus.py": "sha384-x"}}))
        with pytest.raises(RecipeError, match="ask-client.js"):
            site.ask_lock(lock)
    finally:
        server.shutdown()


def test_build_site_end_to_end(tmp_path):
    """Checks the site assembles static pages without test or fragment files, one dataset with its preview, the help from the pinned release's corpus.py with the [help] frame (layout and ask included) added only in its copy of the project and site.toml's Ask base passed, the panel files from that release, config.json, no Ask page, and Help links pointing at the docs on GitHub in a build with an empty Ask base. Covers build_site, fetch_ask, build_help and point_help_at_docs with a stand-in API; this test is not itself tested."""
    dist = tmp_path / "dist"
    make_dataset(dist / "tiny_h264", name="Tiny (H.264)")
    base, lock, server = stand_in_api(tmp_path / "api")
    cfg = tmp_path / "site.toml"
    cfg.write_text('[site]\ndomain="d"\nask_base="' + base + '/"\n[help]\nbar="./site/help-bar.html"\nlayout="desk"\nask={heading="Ask us",suggestions=3}\n'
                   'title={site="S",section="Help"}\n[[help.groups]]\nname="All"\ntopics=["docs/getting-started.md"]\n'
                   '[[datasets]]\nslug="tiny"\nrelease="v0.1.0"\npreview="tiny_h264.zip"\ndownloads=["tiny.zip"]\ndates="Spring 2026"\n')
    out = tmp_path / "out"
    try:
        manifest = site.build_site(cfg, out, dist=dist, ask_lock_path=lock)
    finally:
        server.shutdown()
    d = manifest["datasets"][0]
    assert d["dates"] == "Spring 2026" and d["sources"] == [] and d["lengths"][0]["default"]
    assert d["slug"] == "tiny" and d["downloads"][0]["url"] == "https://github.com/slpenza/sos-forge/releases/download/v0.1.0/tiny.zip"
    assert json.loads((out / "datasets.json").read_text()) == manifest
    assert json.loads((out / "config.json").read_text()) == {"askProjects": ["sos"], "askBase": base}
    seen = json.loads((out / "help" / "index.html").read_text())
    assert seen["key"] == "sos" and seen["ask_base"] == base and seen["block"]["layout"] == "desk"
    assert seen["block"]["ask"] == {"heading": "Ask us", "suggestions": 3} and seen["block"]["bar"] == "./site/help-bar.html"
    assert seen["block"]["groups"][0]["name"] == "All"
    assert {"package.json", "site/help-head.html", "site/help-bar.html", "docs/getting-started.md"} <= set(seen["files"])
    assert not {"layout", "ask", "bar"} & set(json.loads((site.REPO_ROOT / "package.json").read_text())["askAnswer"])
    assert all((out / "ask" / f).read_text() == f"/* {f} v9.9.9 */" for f in site.PANEL_FILES)
    for page in ("index.html", "simulator.html", "404.html", "js/globe.js", "js/ask.js", "css/site.css"):
        assert (out / page).exists(), page
    assert not list(out.rglob("*.test.mjs")) and not (out / "help-head.html").exists() and not (out / "site.toml").exists()
    assert not (out / "ask.html").exists() and not any('ask.html' in f.read_text() for f in out.glob("*.html"))
    bare = site.build_site(cfg, tmp_path / "bare", dist=dist, ask_base="")
    assert bare["datasets"] and json.loads((tmp_path / "bare" / "config.json").read_text()) == {"askProjects": ["sos"]}
    assert not (tmp_path / "bare" / "help").exists() and not (tmp_path / "bare" / "ask").exists()
    docs = 'href="https://github.com/slpenza/sos-forge/blob/main/docs/getting-started.md"'
    for page, links in (("index.html", 2), ("simulator.html", 1), ("404.html", 1)):
        text = (tmp_path / "bare" / page).read_text()
        assert text.count(docs) == links and 'help/"' not in text, page
    assert 'href="help/"' in (out / "index.html").read_text() and docs not in (out / "index.html").read_text()
    for old, msg in (('[site]\ndomain="d"\nask_release="v1"\n', "ask_release"), ('[site]\ndomain="d"\n[help]\nrelease="v1"\n', "release")):
        (tmp_path / "old.toml").write_text(old)
        with pytest.raises(RecipeError, match=msg):
            site.load_config(tmp_path / "old.toml")
    cfg.write_text(cfg.read_text().replace('topics=["docs/getting-started.md"]', 'topics=["docs/nope.md"]'))
    with pytest.raises(RecipeError, match="nope.md"):
        site.load_config(cfg)
