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
    """Checks a release asset is found by name, downloaded once with the token only on the API call, then served from cache, and a missing asset names what exists. Covers release_asset with stubbed HTTP; real GitHub is exercised by releases."""
    calls = []
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as z:
        z.writestr("d/playlist.sos", "data = x.mp4\n")

    def fake(url, headers=None, **kw):
        """Serve release metadata or the asset bytes. Test helper; not itself tested."""
        calls.append((url, dict(headers or {})))
        if "/releases/tags/" in url:
            return json.dumps({"assets": [{"name": "d.zip", "browser_download_url": "https://dl/d.zip"}]}).encode()
        return payload.getvalue()

    monkeypatch.setenv("GH_TOKEN", "t0ken")
    monkeypatch.setattr(site, "http_request", fake)
    p = site.release_asset("o/r", "v0.1.0", "d.zip")
    assert zipfile.is_zipfile(p) and len(calls) == 2
    assert calls[0][1]["Authorization"] == "Bearer t0ken" and "Authorization" not in calls[1][1]
    site.release_asset("o/r", "v0.1.0", "d.zip")
    assert len(calls) == 2
    with pytest.raises(RecipeError, match="has: d.zip"):
        site.release_asset("o/r", "v0.1.0", "other.zip")


def test_build_site_end_to_end(tmp_path):
    """Checks the site assembles static pages without test or fragment files, one dataset with its preview, the help via corpus.py, the panel files and config.json. Covers build_site, build_help and ask_checkout_release with a stand-in ask-answer checkout; this test is not itself tested."""
    dist = tmp_path / "dist"
    make_dataset(dist / "tiny_h264", name="Tiny (H.264)")
    ask = tmp_path / "ask-answer"
    (ask / "client").mkdir(parents=True)
    for f in site.PANEL_FILES:
        (ask / "client" / f).write_text(f"/* {f} */")
    (ask / "tools").mkdir()
    (ask / "tools" / "corpus.py").write_text(
        "import sys, pathlib\nout = pathlib.Path(sys.argv[sys.argv.index('--docs-out') + 1])\n"
        "out.mkdir(parents=True, exist_ok=True)\n(out / 'index.html').write_text('help for ' + sys.argv[3])\n")
    cfg = tmp_path / "site.toml"
    cfg.write_text('[site]\ndomain="d"\nask_release="v0.18.1"\n[[datasets]]\nslug="tiny"\nrelease="v0.1.0"\n'
                   'preview="tiny_h264.zip"\ndownloads=["tiny.zip"]\n')
    out = tmp_path / "out"
    manifest = site.build_site(cfg, out, dist=dist, ask_checkout=ask, ask_base="https://ask.example/prod/")
    d = manifest["datasets"][0]
    assert d["slug"] == "tiny" and d["downloads"][0]["url"] == "https://github.com/slpenza/sos-forge/releases/download/v0.1.0/tiny.zip"
    assert json.loads((out / "datasets.json").read_text()) == manifest
    assert json.loads((out / "config.json").read_text()) == {"askProjects": ["sos"], "askBase": "https://ask.example/prod"}
    assert (out / "help" / "index.html").read_text() == "help for sos"
    assert all((out / "ask" / f).exists() for f in site.PANEL_FILES)
    for page in ("index.html", "simulator.html", "ask.html", "404.html", "js/globe.js", "css/site.css"):
        assert (out / page).exists(), page
    assert not list(out.rglob("*.test.mjs")) and not (out / "help-head.html").exists() and not (out / "site.toml").exists()
    bare = site.build_site(cfg, tmp_path / "bare", dist=dist)
    assert bare["datasets"] and json.loads((tmp_path / "bare" / "config.json").read_text()) == {"askProjects": ["sos"]}
    with pytest.raises(RecipeError, match="pins v0.18.1"):
        site.build_site(cfg, tmp_path / "strict", dist=dist, ask_checkout=ask, strict_ask=True)
