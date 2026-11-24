"""End-to-end builds of small synthetic recipes: texture, movie (spawned workers + ffmpeg), caching and strictness."""
from __future__ import annotations

import pathlib

import cv2
import pytest

from conftest import earth_png, gpx_loop, write_recipe
from sosforge.build import (BuildError, BuildOptions, FrameRenderer, apply_text_style, build, length_tag, make_context,
                            make_layers, prepare_layers, still)
from sosforge.recipe import RecipeError, load_recipe
from sosforge.site import bundle

TEXTURE = '''[dataset]
name = "Tiny Texture"
description = "A test."
width = 256
credits = ["Test credit"]
license = "CC0"
[[layers]]
type = "basemap"
path = "earth.png"
brightness = 0.5
[[layers]]
type = "tracks"
source = { type = "tracks", path = "data/*.gpx", attribution = "My GPS" }
width_km = 80
[[layers]]
type = "points"
source = { type = "csv", path = "pts.csv", attribution = "Pinball Map (test)" }
[hud]
title = "{count} activities"
subtitle = "{distance_km:,.0f} km"
'''
MOVIE = '''[dataset]
name = "Tiny Movie"
output = "movie"
width = 256
[animation]
frames = 6
fps = 30
loop = false
start = "2000-01-01"
end = "2010-01-01"
[encode]
preset = "ultrafast"
[[layers]]
type = "basemap"
path = "earth.png"
[[layers]]
type = "points"
source = { type = "csv", path = "pts.csv" }
time = "when"
flash_days = 300
radius_km = 300
[hud]
title = "Growth"
subtitle = "{date:%Y}  {count} places"
progress = true
'''
CSV = "lat,lon,when\n0,0,2001-01-01\n30,60,2005-01-01\n-20,-60,2009-01-01\n"


def make(tmp_path, toml):
    """Write a recipe with the synthetic basemap, points CSV and two GPX files. Helper; not itself tested."""
    d = write_recipe(tmp_path / "r", toml, {"pts.csv": CSV, "data/a.gpx": gpx_loop(47.6, -122.3, 20000),
                                            "data/b.gpx": gpx_loop(-33.9, 151.2, 20000)})
    earth_png(d / "earth.png", 512)
    return d


def test_texture_build_end_to_end(tmp_path):
    """Checks a texture recipe renders, passes every check, writes a 2:1 JPEG and packages credits and licence notes. Covers build, make_context, collect_checks, gather_credits and build_package; this test is not itself tested."""
    s = build(make(tmp_path, TEXTURE), BuildOptions(out_dir=tmp_path / "dist", build_dir=tmp_path / "b", workers=1))
    assert all(c["passed"] for c in s["checks"]), [c for c in s["checks"] if not c["passed"]]
    img = cv2.imread(s["files"]["data"])
    assert img.shape == (128, 256, 3) and s["files"]["data"].endswith("tiny_texture_256x128.jpg")
    assert s["credits"][:3] == ["Test credit", "My GPS", "Pinball Map (test)"] and s["licenses"] == ["CC0"]
    pl = (tmp_path / "dist" / "tiny_texture" / "playlist.sos").read_text()
    assert "data = tiny_texture_256x128.jpg" in pl and "fps" not in pl and "Credits: Test credit; My GPS" in pl


def test_name_takes_build_values(tmp_path):
    """Checks a dataset name can name build values as HUD templates do, filled from the first frame into the playlist and README, that one naming a missing value fails naming the ones available, and that such a name needs a slug. Covers dataset_name, FrameRenderer.values, build and load_recipe; which values a real source supplies is tested with each source."""
    hook = "from datetime import datetime\n\ndef values(t, ctx):\n    return {'when': datetime(2026, 12, 5, 2, 6)}\n"
    named = TEXTURE.replace('name = "Tiny Texture"', 'name = "Tiny, {when.day} {when:%b %Y}"\nslug = "tiny_texture"')
    d = make(tmp_path, named)
    (d / "recipe.py").write_text(hook)
    build(d, BuildOptions(out_dir=tmp_path / "dist", build_dir=tmp_path / "b", workers=1))
    folder = tmp_path / "dist" / "tiny_texture"
    assert "name = Tiny, 5 Dec 2026\n" in (folder / "playlist.sos").read_text()
    assert (folder / "README.md").read_text().startswith("# Tiny, 5 Dec 2026\n")
    (d / "recipe.toml").write_text(named.replace("{when.day}", "{whenever.day}"))
    with pytest.raises(RecipeError, match=r"\[dataset\] name .*available values are: .*when"):
        build(d, BuildOptions(out_dir=tmp_path / "dist", build_dir=tmp_path / "b", workers=1))
    (d / "recipe.toml").write_text(named.replace('slug = "tiny_texture"\n', ""))
    with pytest.raises(RecipeError, match="needs a slug"):
        load_recipe(d)


def test_movie_build_end_to_end(tmp_path):
    """Checks a timed movie renders once in spawned workers into MPEG-4 (primary) and H.264 (variant folder), both validate, a single-codec override works, and a still renders. Covers encode_movie, build packaging of variants, _init_worker, _render_task, probe_video, validate_video and still; this test is not itself tested."""
    d = make(tmp_path, MOVIE)
    opts = BuildOptions(out_dir=tmp_path / "dist", build_dir=tmp_path / "b", workers=2, frames_dir=tmp_path / "frames")
    s = build(d, opts)
    assert all(c["passed"] for c in s["checks"]), [c for c in s["checks"] if not c["passed"]]
    assert set(s["videos"]) == {"mpeg4", "h264"} and s["video"]["codec"] == "mpeg4"
    for codec, v in s["videos"].items():
        assert (v["codec"], v["width"], v["height"], v["frames"]) == (codec, 256, 128, 6) and v["frame_types"].get("B", 0) == 0
    assert len(list((tmp_path / "frames").glob("*.png"))) == 6
    primary = (tmp_path / "dist" / "tiny_movie" / "playlist.sos").read_text()
    variant = (tmp_path / "dist" / "tiny_movie_h264" / "playlist.sos").read_text()
    assert "data = tiny_movie_256x128.mp4" in primary and "fps = 30" in primary and "name = Tiny Movie\n" in primary
    assert "data = tiny_movie_256x128_h264.mp4" in variant and "name = Tiny Movie (H.264)" in variant
    assert "label = labels.txt\nlabelvisible = false" in primary and "label = labels.txt" in variant
    lines = (tmp_path / "dist" / "tiny_movie" / "labels.txt").read_text().splitlines()
    assert len(lines) == 6 and lines[0] == "Growth  \u00b7  2000 0 places" and lines[-1] == "Growth  \u00b7  2010 3 places"
    assert (tmp_path / "dist" / "tiny_movie_h264" / "labels.txt").read_text().splitlines() == lines
    only = build(d, BuildOptions(out_dir=tmp_path / "d2", build_dir=tmp_path / "b", workers=1, codecs=["h264"]))
    assert set(only["videos"]) == {"h264"} and not only["variants"] and not (tmp_path / "d2" / "tiny_movie_h264").exists()
    png = still(d, BuildOptions(build_dir=tmp_path / "b", workers=1), 5, tmp_path / "s.png")
    assert cv2.imread(str(png)).shape == (128, 256, 3)


def test_length_variant_build(tmp_path):
    """Checks --seconds renders that many seconds of frames and --hud labels switches the text to SOS labels, both tagging folders, files and playlist names so variants install side by side, bundles them under tagged names, leaves the recipe's own length and style untagged, and refuses bad combinations. Covers make_context, length_tag, apply_text_style, the tagged build outputs, playlist_text, version_note and bundle; this test is not itself tested."""
    d = make(tmp_path, MOVIE)
    s = build(d, BuildOptions(out_dir=tmp_path / "dist", build_dir=tmp_path / "b", workers=1, seconds=1))
    assert all(c["passed"] for c in s["checks"]) and s["videos"]["mpeg4"]["frames"] == 30
    primary = (tmp_path / "dist" / "tiny_movie_1s" / "playlist.sos").read_text()
    variant = (tmp_path / "dist" / "tiny_movie_1s_h264" / "playlist.sos").read_text()
    assert "name = Tiny Movie (1 s)\n" in primary and "data = tiny_movie_1s_256x128.mp4" in primary
    assert "name = Tiny Movie (1 s, H.264)" in variant and "data = tiny_movie_1s_256x128_h264.mp4" in variant
    readme = (tmp_path / "dist" / "tiny_movie_1s" / "README.md").read_text()
    assert "This is the **1 s** version (30 frames at 30 fps)" in readme and "`tiny_movie_1s/`" in (
        tmp_path / "dist" / "tiny_movie_1s_h264" / "README.md").read_text()
    assert len((tmp_path / "dist" / "tiny_movie_1s" / "labels.txt").read_text().splitlines()) == 30
    lab = build(d, BuildOptions(out_dir=tmp_path / "dist", build_dir=tmp_path / "b", workers=2, seconds=1, hud="labels",
                                codecs=["mpeg4"]))
    first = lambda p: cv2.VideoCapture(str(p)).read()[1].astype(float)
    painted = first(tmp_path / "dist" / "tiny_movie_1s" / "tiny_movie_1s_256x128.mp4")
    plain = first(tmp_path / "dist" / "tiny_movie_1s_labels" / "tiny_movie_1s_labels_256x128.mp4")
    expect = {}
    for mode in ("labels", "panels"):
        r = load_recipe(d)
        r.hud["mode"] = mode
        ctx = make_context(r, BuildOptions(build_dir=tmp_path / "b", workers=1, seconds=1))
        layers = make_layers(ctx)
        prepare_layers(ctx, layers)
        expect[mode] = FrameRenderer(ctx, layers).frame(0)[..., ::-1].astype(float)
    hud = abs(expect["labels"] - expect["panels"]).max(-1) > 30
    assert hud.any()
    assert abs(plain - expect["labels"])[hud].mean() < abs(plain - expect["panels"])[hud].mean(), "workers drew panels"
    assert abs(painted - expect["panels"])[hud].mean() < abs(painted - expect["labels"])[hud].mean()
    assert all(c["passed"] for c in lab["checks"]) and not (tmp_path / "dist" / "tiny_movie_1s_labels_h264").exists()
    lp = (tmp_path / "dist" / "tiny_movie_1s_labels" / "playlist.sos").read_text()
    assert "name = Tiny Movie (1 s, SOS labels)\n" in lp and "data = tiny_movie_1s_labels_256x128.mp4" in lp
    assert "labelvisible = true" in lp and "labelvisible = false" in primary
    assert "SOS draws it from `labels.txt`" in (tmp_path / "dist" / "tiny_movie_1s_labels" / "README.md").read_text()
    assert sorted(z.name for z in bundle("tiny_movie", tmp_path / "dist")) == [
        "tiny_movie_1s_256x128.zip", "tiny_movie_1s_256x128_h264.zip", "tiny_movie_1s_labels_256x128.zip",
        "tiny_movie_1s_labels_web.zip", "tiny_movie_1s_web.zip"]
    r = load_recipe(d)
    same = BuildOptions(build_dir=tmp_path / "b", seconds=1, fps=6)
    assert length_tag(r, make_context(r, same), same) is None
    for opts, msg in ((BuildOptions(seconds=1, frames=12), "not both"), (BuildOptions(seconds=0), "1 or more")):
        with pytest.raises(RecipeError, match=msg):
            make_context(r, opts)
    with pytest.raises(RecipeError, match="still image"):
        make_context(load_recipe(make(tmp_path / "t", TEXTURE)), BuildOptions(seconds=10))
    assert apply_text_style(load_recipe(d), BuildOptions(hud="panels")) is None
    with pytest.raises(RecipeError, match="panels .* or labels"):
        apply_text_style(load_recipe(d), BuildOptions(hud="subtitles"))
    with pytest.raises(RecipeError, match="no movie text"):
        apply_text_style(load_recipe(make(tmp_path / "t2", TEXTURE)), BuildOptions(hud="labels"))


def test_cache_reused_and_invalidated(tmp_path, caplog):
    """Checks a second build reuses every layer cache and editing a data file rebuilds only that layer. Covers prepare_layers and Layer.key/inputs; this test is not itself tested."""
    d = make(tmp_path, TEXTURE)
    opts = BuildOptions(out_dir=tmp_path / "dist", build_dir=tmp_path / "b", workers=1)
    build(d, opts)
    caplog.clear()
    caplog.set_level("INFO")
    build(d, opts)
    assert caplog.text.count("reusing cached layer") == 3
    (d / "pts.csv").write_text(CSV + "10,10,2003-01-01\n")
    caplog.clear()
    build(d, opts)
    assert caplog.text.count("reusing cached layer") == 2 and "preparing layer 3 (points)" in caplog.text


def test_strict_failure_raises(tmp_path):
    """Checks strict builds stop when a check fails (no tracks survive trimming) and --no-strict proceeds. Covers build strictness; this test is not itself tested."""
    toml = TEXTURE.replace("width_km = 80", "width_km = 80\nprivacy_trim_m = 9999999")
    d = make(tmp_path, toml)
    with pytest.raises(BuildError, match="verification failed"):
        build(d, BuildOptions(out_dir=tmp_path / "dist", build_dir=tmp_path / "b", workers=1))
    s = build(d, BuildOptions(out_dir=tmp_path / "dist", build_dir=tmp_path / "b", workers=1, strict=False))
    assert not all(c["passed"] for c in s["checks"])


def test_labels_mode_skips_panels(tmp_path):
    """Checks [hud] mode = "labels" bakes no panels into frames but still writes visible SOS labels, and textures get no labels. Covers FrameRenderer.label, labels-mode rendering and playlist label lines; this test is not itself tested."""
    from sosforge.build import FrameRenderer, make_context, make_layers, prepare_layers
    from sosforge.recipe import load_recipe
    d = make(tmp_path, MOVIE.replace('progress = true', 'progress = true\nmode = "labels"').replace("frames = 6", "frames = 2"))
    r = load_recipe(d)
    ctx = make_context(r, BuildOptions(build_dir=tmp_path / "b", workers=1))
    layers = make_layers(ctx)
    prepare_layers(ctx, layers)
    plain = FrameRenderer(ctx, layers)
    assert plain.hud is None and plain.label(1) == "Growth  \u00b7  2010 3 places"
    r2 = load_recipe(make(tmp_path / "p", MOVIE.replace("frames = 6", "frames = 2")))
    ctx2 = make_context(r2, BuildOptions(build_dir=tmp_path / "b2", workers=1))
    l2 = make_layers(ctx2)
    prepare_layers(ctx2, l2)
    assert (FrameRenderer(ctx2, l2).frame(1) != plain.frame(1)).any()
    s = build(d, BuildOptions(out_dir=tmp_path / "dist", build_dir=tmp_path / "b", workers=1, codecs=["h264"]))
    assert "labelvisible = true" in pathlib.Path(s["files"]["playlist"]).read_text()
    t = build(make(tmp_path / "t", TEXTURE), BuildOptions(out_dir=tmp_path / "dt", build_dir=tmp_path / "bt", workers=1))
    assert "label" not in pathlib.Path(t["files"]["playlist"]).read_text() and not (tmp_path / "dt" / "tiny_texture" / "labels.txt").exists()
