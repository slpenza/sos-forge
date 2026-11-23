"""Tests for built-in layers using small synthetic recipes (no network)."""
from __future__ import annotations

import math

import cv2
import numpy as np
import pytest
import tifffile

from conftest import earth_png, gpx_loop, write_recipe
from sosforge import basemaps
from sosforge.build import BuildOptions, FrameRenderer, make_context, make_layers, prepare_layers
from sosforge.context import FieldStore
from sosforge.grid import lon_to_x, parse_color
from sosforge.layers.lit_globe import load_elevation
from sosforge.layers.tracks import colormap, densify, trim_ends
from sosforge.recipe import RecipeError, load_recipe


def setup(tmp_path, toml: str, files=None, width: int = 256, frames: int | None = None):
    """Load a recipe from text, build context and layers, prepare them, and return (ctx, layers, renderer). Helper; not itself tested."""
    r = load_recipe(write_recipe(tmp_path / "r", toml, files))
    ctx = make_context(r, BuildOptions(width=width, frames=frames, build_dir=tmp_path / "b", workers=1))
    layers = make_layers(ctx)
    prepare_layers(ctx, layers)
    return ctx, layers, FrameRenderer(ctx, layers)


def test_field_store_round_trip(tmp_path):
    """Checks arrays and JSON round-trip, memory-mapping works, and the completion marker toggles. Covers FieldStore; this test is not itself tested."""
    s = FieldStore(tmp_path / "s")
    s.put("a", np.arange(6.0).reshape(2, 3))
    s.put_json("m", {"x": [1, 2]})
    assert s.has("a") and not s.has("b") and isinstance(s.get("a"), np.memmap)
    assert s.get("a", mmap=False).sum() == 15 and s.get_json("m") == {"x": [1, 2]}
    assert not s.complete
    s.mark_complete()
    assert s.complete


@pytest.mark.parametrize("layer,msg", [
    ('type="basemap"', "exactly one"),
    ('type="basemap"\nname="mars"', "unknown basemap"),
    ('type="points"\nsource={type="csv",path="x.csv"}\ncycle=true', "needs category"),
    ('type="points"', "'source' is required"),
    ('type="lit_globe"\ncolor="c"\nelevation="e"\nradius_km=1\nbrdf="phong"', "brdf"),
    ('type="tracks"\nsource={type="tracks"}\nwidht_km=3', "did you mean 'width_km'"),
])
def test_layer_option_validation(tmp_path, layer, msg):
    """Checks each layer rejects bad option combinations with a specific message. Covers Layer.__init__ and subclass validation; this test is not itself tested."""
    r = load_recipe(write_recipe(tmp_path / "r", f'[dataset]\nname="v"\n[[layers]]\n{layer}\n'))
    with pytest.raises(RecipeError, match=msg):
        make_layers(make_context(r, BuildOptions(width=64, build_dir=tmp_path / "b")))


def test_basemap_from_path_and_color(tmp_path):
    """Checks a file basemap resamples in linear light with brightness, a colour basemap fills, and both pass the seam check. Covers BasemapLayer.prepare, render, checks and load_rgb; this test is not itself tested."""
    png = earth_png(tmp_path / "r" / "earth.png", 512)
    toml = '[dataset]\nname="b"\n[[layers]]\ntype="basemap"\npath="earth.png"\nbrightness=0.5\n[hud]\nenabled=false\n'
    ctx, layers, fr = setup(tmp_path, toml)
    canvas = np.zeros((128, 256, 3), np.float32)
    layers[0].render(canvas, ctx.timeline.at(0))
    src = cv2.resize(cv2.imread(str(png))[..., ::-1], (256, 128), interpolation=cv2.INTER_AREA) / 255.0
    from sosforge.grid import srgb_to_linear
    assert np.abs(canvas - 0.5 * srgb_to_linear(src)).max() < 0.02
    assert all(c.passed for c in layers[0].checks())
    ctx2, layers2, _ = setup(tmp_path / "c", '[dataset]\nname="c"\n[[layers]]\ntype="basemap"\ncolor="#ff0000"\nopacity=0.5\n[hud]\nenabled=false\n')
    c2 = np.ones((128, 256, 3), np.float32)
    layers2[0].render(c2, ctx2.timeline.at(0))
    assert np.allclose(c2[0, 0], [1.0, 0.5, 0.5], atol=1e-3)


def test_basemap_named_uses_gibs_request(tmp_path, monkeypatch):
    """Checks a named basemap requests an EPSG:4326 whole-globe GIBS image at the output width, caches it and credits NASA. Covers fetch_basemap, wms_params and basemap_credit with stubbed HTTP."""
    calls = []

    def fake(url, params=None, **kw):
        """Return a PNG of the requested size. Helper; not itself tested."""
        calls.append(params)
        ok, buf = cv2.imencode(".png", np.full((params["HEIGHT"], params["WIDTH"], 3), 40, np.uint8))
        return buf.tobytes()

    monkeypatch.setattr(basemaps, "http_request", fake)
    p = basemaps.fetch_basemap("black_marble", 512)
    assert calls[0]["LAYERS"] == "VIIRS_Black_Marble" and calls[0]["BBOX"] == "-180,-90,180,90" and calls[0]["WIDTH"] == 512
    basemaps.fetch_basemap("black_marble", 512)
    assert len(calls) == 1 and p.exists() and "GIBS" in basemaps.basemap_credit("black_marble")
    assert basemaps.wms_params("x", 8192)["FORMAT"] == "image/jpeg"

    def bad(url, params=None, **kw):
        """Return an XML error instead of an image. Helper; not itself tested."""
        return b"<ServiceException/>"

    monkeypatch.setattr(basemaps, "http_request", bad)
    with pytest.raises(RuntimeError, match="unusable"):
        basemaps.fetch_basemap("blue_marble", 256)


POINTS = '''[dataset]
name = "p"
[[layers]]
type = "basemap"
color = "#000000"
[[layers]]
type = "points"
source = {{ type = "csv", path = "pts.csv" }}
{extra}
[hud]
enabled = false
'''
CSV = "lat,lon,kind,n,when\n0,0,A,1,2001-01-01\n0,90,B,4,2005-06-01\n45,-120,A,9,2010-01-01\n91,0,A,1,2001-01-01\n"


def test_points_static_glow_and_values(tmp_path):
    """Checks static points glow at their locations in their palette colour, bad rows are dropped, and values count points. Covers PointsLayer.prepare, _glow, render, values, checks and credits; this test is not itself tested."""
    extra = 'category = "kind"\npalette = { A = "#ff0000", B = "#0000ff" }\nradius_km = 300\nhalo_strength = 0\nattribution = "Me"'
    ctx, layers, fr = setup(tmp_path, POINTS.format(extra=extra), {"pts.csv": CSV})
    pts = layers[1]
    canvas = np.zeros((128, 256, 3), np.float32)
    pts.render(canvas, ctx.timeline.at(0))
    xa, xb = int(lon_to_x(0, 256)), int(lon_to_x(math.pi / 2, 256))
    assert canvas[64, xa, 0] > 0.3 and canvas[64, xa, 2] < 0.05 and canvas[64, xb, 2] > 0.3
    assert pts.values(ctx.timeline.at(0))["count"] == 3
    c = pts.checks()[0]
    assert c.passed and "1 rows dropped" in c.detail and pts.credits() == ["Me"]


def test_points_cycle_spotlight(tmp_path):
    """Checks the spotlight cycle highlights each category in turn, ends on all, crossfades, and labels the HUD. Covers PointsLayer._segment and values with cycle; this test is not itself tested."""
    extra = 'category = "kind"\ncycle = true\ncycle_all_label = "Everything"\ncycle_dim = 0.1'
    ctx, layers, fr = setup(tmp_path, POINTS.format(extra=extra).replace('name = "p"', 'name = "p"\noutput = "movie"'),
                            {"pts.csv": CSV}, frames=30)
    pts = layers[1]
    a0, l0 = pts._segment(ctx.timeline.at(1))
    a1, l1 = pts._segment(ctx.timeline.at(12))
    a2, l2 = pts._segment(ctx.timeline.at(22))
    assert a0.tolist() == pytest.approx([1.0, 0.1]) and l0 == 0 and a1.tolist() == pytest.approx([0.1, 1.0]) and l1 == 1
    assert a2.tolist() == pytest.approx([1.0, 1.0]) and pts.values(ctx.timeline.at(22))["category"] == "Everything"
    assert pts.values(ctx.timeline.at(1)) == {"count": 2, "weight_sum": 2.0, "category": "A", "categories": 2}
    mid, _ = pts._segment(ctx.timeline.at(9))
    assert 0.1 < mid[0] < 1.0


def test_points_time_reveal(tmp_path):
    """Checks timed points appear on their date, counts grow, weights sum, and the flash brightens new points. Covers PointsLayer time mode, _now_days and flash rendering; this test is not itself tested."""
    toml = POINTS.format(extra='weight = "n"\ntime = "when"\nradius_km = 300\nhalo_strength = 0\nflash_days = 800')
    toml = toml.replace('name = "p"', 'name = "p"\noutput = "movie"') + '[animation]\nframes = 5\nloop = false\nstart = "2000-01-01"\nend = "2010-06-01"\n'
    ctx, layers, fr = setup(tmp_path, toml, {"pts.csv": CSV}, frames=5)
    pts = layers[1]
    counts = [pts.values(ctx.timeline.at(i))["count"] for i in range(5)]
    assert counts == sorted(counts) and counts[0] == 0 and counts[-1] == 3
    assert pts.values(ctx.timeline.at(4))["weight_sum"] == pytest.approx(14.0)
    xa = int(lon_to_x(0, 256))
    t_new = ctx.timeline.at(1)
    assert pts.values(t_new)["count"] == 1
    c = np.zeros((128, 256, 3), np.float32)
    pts.render(c, t_new)
    plain = np.zeros_like(c)
    pts.opts["flash_days"] = 0
    pts.render(plain, t_new)
    assert c[64, xa].sum() > plain[64, xa].sum() > 0


def test_colormap_and_trim():
    """Checks colormap endpoints, single-colour ramps, privacy trimming and great-circle densification. Covers colormap, trim_ends and densify; this test is not itself tested."""
    assert np.allclose(colormap(np.array([0.0]), "fire"), 0) and colormap(np.array([1.0]), "fire").min() > 0.6
    assert np.allclose(colormap(np.array([0.5]), "#ffffff"), 0.5)
    with pytest.raises(RecipeError):
        colormap(np.array([0.5]), "nope")
    lat = np.linspace(0, 0.02, 21)
    lon = np.zeros(21)
    tl, _ = trim_ends(lat, lon, 400)
    assert tl.min() > 0.0035 and tl.max() < 0.0165 and len(trim_ends(lat[:3], lon[:3], 400)[0]) == 0
    dl, dn = densify(np.array([0.0, 0.0]), np.array([0.0, 1.0]), 10_000)
    assert len(dl) == 13 and np.allclose(dn[[0, -1]], [0, 1])


def test_tracks_glow_inset_and_values(tmp_path):
    """Checks tracks glow at their city, the inset sits opposite with a label, values report counts, and checks pass. Covers TracksLayer prepare, render, overlay, values, checks and _inset_anchor; this test is not itself tested."""
    toml = ('[dataset]\nname="t"\n[[layers]]\ntype="basemap"\ncolor="#000000"\n[[layers]]\ntype="tracks"\n'
            'source={type="tracks",path="data/*.gpx"}\nwidth_km=60\nprivacy_trim_m=200\ninset_size_deg=40\n[hud]\nenabled=false\n')
    files = {"data/a.gpx": gpx_loop(47.6, -122.3, 8000), "data/b.gpx": gpx_loop(47.62, -122.35, 5000)}
    ctx, layers, fr = setup(tmp_path, toml, files, width=512)
    tr = layers[1]
    v = tr.values(ctx.timeline.at(0))
    assert v["count"] == 2 and 70 < v["distance_km"] < 90 and v["span_km"] > 10
    canvas = np.zeros((256, 512, 3), np.float32)
    tr.render(canvas, ctx.timeline.at(0))
    y, x = int((90 - 47.6) / 180 * 256), int((-122.3 + 180) / 360 * 512)
    assert canvas[y - 2:y + 3, x - 2:x + 3].max() > 0.2 and canvas[:, 300:400].max() < 0.01
    frame = fr.frame(0)
    lon_in, lat_in = tr._inset_anchor(tr.stats)
    assert abs(lon_in - 57.7) < 1 and lat_in == 20
    xi, yi = int((lon_in + 180) / 360 * 512), int((90 - lat_in) / 180 * 256)
    assert frame[yi - 5:yi + 5, xi - 5:xi + 5].max() > 0 and tr.checks()[0].passed


def test_lit_globe_elevation_units(tmp_path):
    """Checks km, metre and SVS uint16 elevation encodings and explicit scale overrides convert to metres. Covers load_elevation; this test is not itself tested."""
    tifffile.imwrite(tmp_path / "f.tif", np.full((2, 4), 1.5, np.float32))
    tifffile.imwrite(tmp_path / "u.tif", np.array([[20000, 22000]], np.uint16))
    assert np.allclose(load_elevation(tmp_path / "f.tif", "km", None, 0.0), 1500.0)
    assert np.allclose(load_elevation(tmp_path / "u.tif", "svs_uint16", None, 0.0), [[0.0, 1000.0]])
    assert np.allclose(load_elevation(tmp_path / "f.tif", "m", 2.0, 10.0), 13.0)
    with pytest.raises(RecipeError):
        load_elevation(tmp_path / "f.tif", "furlongs", None, 0.0)


def test_lit_globe_sun_schedule_and_centroid(tmp_path):
    """Checks the subsolar longitude schedule, that sun-at-0 lights X = W/2, shading respects ambient, and checks pass on a flat synthetic body. Covers LitGlobeLayer.sun_lon, shading, centroid_x, render and checks; this test is not itself tested."""
    d = tmp_path / "r"
    earth_png(d / "albedo.png", 256)
    tifffile.imwrite(d / "dem.tif", np.zeros((64, 128), np.float32))
    toml = ('[dataset]\nname="g"\noutput="movie"\n[animation]\nframes=8\n[[layers]]\ntype="lit_globe"\ncolor="albedo.png"\n'
            'elevation="dem.tif"\nradius_km=1000\nshadows=true\nambient=0.0\n[hud]\nenabled=false\n')
    ctx, layers, fr = setup(tmp_path, toml, width=128, frames=8)
    g = layers[0]
    assert [round(math.degrees(g.sun_lon(f)), 6) for f in (0, 0.25, 0.5, 0.75)] == [-180.0, 90.0, 0.0, -90.0]
    assert g.centroid_x(0.0) == pytest.approx(64, abs=0.5) and all(c.passed for c in g.checks())
    canvas = np.zeros((64, 128, 3), np.float32)
    g.render(canvas, ctx.timeline.at(4))
    assert canvas[32, 64].sum() > 0 and canvas[32, 0].sum() == 0
    assert g.values(ctx.timeline.at(2))["sun_lon_deg"] == pytest.approx(90.0)
    assert parse_color("#000000").sum() == 0
