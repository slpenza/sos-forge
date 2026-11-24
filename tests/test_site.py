"""Tests for the sos.stevenpenza.com site builder: playlists, bundles, previews, release downloads and assembly."""
from __future__ import annotations

import io
import json
import subprocess
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
    ok.write_text('[site]\ndomain="d"\nask_release="v1"\n[[datasets]]\nslug="a"\nrelease="v0.1.0"\npreview="a.zip"\n')
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


def git(repo, *args):
    """Run a git command in a test repo with a fixed identity. Helper; not itself tested."""
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
                   check=True, capture_output=True)


def stand_in_ask_answer(root):
    """Make a git repo standing in for ask-answer: v0.18.1 has the panel files and a corpus.py that refuses a frame, v0.19.1 a corpus.py that records the askAnswer block it reads, and the working tree is left on an untagged later commit. Helper; not itself tested."""
    (root / "client").mkdir(parents=True)
    (root / "tools").mkdir()
    for f in site.PANEL_FILES:
        (root / "client" / f).write_text(f"/* {f} v0.18.1 */")
    head = "import json, pathlib, sys\nproject = pathlib.Path(sys.argv[sys.argv.index('--project') + 1])\n" \
           "block = json.loads((project / 'package.json').read_text())['askAnswer']\n" \
           "out = pathlib.Path(sys.argv[sys.argv.index('--docs-out') + 1])\nout.mkdir(parents=True, exist_ok=True)\n"
    (root / "tools" / "corpus.py").write_text(head + "if 'bar' in block: sys.exit('askAnswer.bar is not a field')\n"
                                              "(out / 'index.html').write_text('v0.18.1')\n")
    git(root, "init", "-q")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "v0.18.1")
    git(root, "tag", "v0.18.1")
    (root / "tools" / "corpus.py").write_text(
        head + "(out / 'index.html').write_text(json.dumps({'key': sys.argv[3], 'block': block, "
        "'files': sorted(str(p.relative_to(project)) for p in project.rglob('*') if p.is_file())}))\n")
    for f in site.PANEL_FILES:
        (root / "client" / f).write_text(f"/* {f} v0.19.1 */")
    git(root, "commit", "-qam", "v0.19.1")
    git(root, "tag", "v0.19.1")
    (root / "client" / site.PANEL_FILES[0]).write_text("/* work in progress */")
    git(root, "commit", "-qam", "later")


def test_build_site_end_to_end(tmp_path):
    """Checks the site assembles static pages without test or fragment files, one dataset with its preview, the help from the [help] release's corpus.py with the frame added only in its copy of the project, the panel files from ask_release whatever the checkout has out, both from one tag when the pins match, config.json, no Ask page, Help links pointing at the docs on GitHub in a build without an ask-answer checkout, and that a missing tag fails a strict build. Covers build_site, build_help, release_tree and ask_release_files with a stand-in ask-answer repo; this test is not itself tested."""
    dist = tmp_path / "dist"
    make_dataset(dist / "tiny_h264", name="Tiny (H.264)")
    ask = tmp_path / "ask-answer"
    stand_in_ask_answer(ask)
    cfg = tmp_path / "site.toml"
    cfg.write_text('[site]\ndomain="d"\nask_release="v0.18.1"\n[help]\nrelease="v0.19.1"\nbar="./site/help-bar.html"\n'
                   'title={site="S",section="Help"}\n[[help.groups]]\nname="All"\ntopics=["docs/getting-started.md"]\n'
                   '[[datasets]]\nslug="tiny"\nrelease="v0.1.0"\npreview="tiny_h264.zip"\ndownloads=["tiny.zip"]\ndates="Spring 2026"\n')
    out = tmp_path / "out"
    manifest = site.build_site(cfg, out, dist=dist, ask_checkout=ask, ask_base="https://ask.example/prod/", strict_ask=True)
    d = manifest["datasets"][0]
    assert d["dates"] == "Spring 2026" and d["sources"] == [] and d["lengths"][0]["default"]
    assert d["slug"] == "tiny" and d["downloads"][0]["url"] == "https://github.com/slpenza/sos-forge/releases/download/v0.1.0/tiny.zip"
    assert json.loads((out / "datasets.json").read_text()) == manifest
    assert json.loads((out / "config.json").read_text()) == {"askProjects": ["sos"], "askBase": "https://ask.example/prod"}
    seen = json.loads((out / "help" / "index.html").read_text())
    assert seen["key"] == "sos" and seen["block"]["bar"] == "./site/help-bar.html" and seen["block"]["groups"][0]["name"] == "All"
    assert {"package.json", "site/help-head.html", "site/help-bar.html", "docs/getting-started.md"} <= set(seen["files"])
    assert "bar" not in json.loads((site.REPO_ROOT / "package.json").read_text())["askAnswer"]
    assert all((out / "ask" / f).read_text() == f"/* {f} v0.18.1 */" for f in site.PANEL_FILES)
    for page in ("index.html", "simulator.html", "404.html", "js/globe.js", "css/site.css"):
        assert (out / page).exists(), page
    assert not list(out.rglob("*.test.mjs")) and not (out / "help-head.html").exists() and not (out / "site.toml").exists()
    assert not (out / "ask.html").exists() and not any('ask.html' in f.read_text() for f in out.glob("*.html"))
    bare = site.build_site(cfg, tmp_path / "bare", dist=dist)
    assert bare["datasets"] and json.loads((tmp_path / "bare" / "config.json").read_text()) == {"askProjects": ["sos"]}
    docs = 'href="https://github.com/slpenza/sos-forge/blob/main/docs/getting-started.md"'
    for page, links in (("index.html", 2), ("simulator.html", 1), ("404.html", 1)):
        text = (tmp_path / "bare" / page).read_text()
        assert text.count(docs) == links and 'help/"' not in text, page
    assert 'href="help/"' in (out / "index.html").read_text() and docs not in (out / "index.html").read_text()
    same = tmp_path / "same.toml"
    same.write_text(cfg.read_text().replace('[help]\nrelease="v0.19.1"', '[help]').replace('ask_release="v0.18.1"', 'ask_release="v0.19.1"'))
    assert site.load_config(same)["help_release"] == "v0.19.1"
    site.build_site(same, tmp_path / "same", dist=dist, ask_checkout=ask, strict_ask=True)
    assert all((tmp_path / "same" / "ask" / f).read_text() == f"/* {f} v0.19.1 */" for f in site.PANEL_FILES)
    assert json.loads((tmp_path / "same" / "help" / "index.html").read_text())["block"]["bar"] == "./site/help-bar.html"
    cfg.write_text(cfg.read_text().replace('release="v0.19.1"', 'release="v0.20.0"'))
    with pytest.raises(RecipeError, match="no v0.20.0"):
        site.build_site(cfg, tmp_path / "strict", dist=dist, ask_checkout=ask, strict_ask=True)
    cfg.write_text(cfg.read_text().replace('topics=["docs/getting-started.md"]', 'topics=["docs/nope.md"]'))
    with pytest.raises(RecipeError, match="nope.md"):
        site.load_config(cfg)
