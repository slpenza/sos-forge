"""Tests for the v0.3.0 layers: daylight, grid and regions."""
from __future__ import annotations

import math
from datetime import datetime, timezone

import cv2
import numpy as np
import pytest

from conftest import write_recipe
from sosforge.build import BuildOptions, FrameRenderer, make_context, make_layers, prepare_layers
from sosforge.grid import lon_lat_axes, lon_to_x, seam_ratio
from sosforge.layers.gridlayer import sample_field
from sosforge.layers.regions import nice_ticks, rasterise
from sosforge.recipe import RecipeError, load_recipe
from sosforge.registry import register_source
from sosforge.sources.base import GridField, RegionSet


@register_source("test_grid")
def _test_grid(opts, ctx):
    """Serve a 1-degree grid with one hot cell at lon 10, lat 65. Test source; not itself tested."""
    v = np.zeros((181, 360), np.float32)
    v[65 + 90, 10] = 80
    return GridField(v, 0.0, 1.0, -90.0, 1.0, {"forecast_time": datetime(2026, 11, 23, 1, 56, tzinfo=timezone.utc)},
                     "Test grid", "PD", "fp")


def square(lon0, lat0, size):
    """Return a closed square ring in lon/lat. Helper; not itself tested."""
    return np.array([[lon0, lat0], [lon0 + size, lat0], [lon0 + size, lat0 + size], [lon0, lat0 + size], [lon0, lat0]], float)


@register_source("test_regions")
def _test_regions(opts, ctx):
    """Serve A (20 deg square at the origin), B (10 deg square in the east) and an island territory sharing A's code. Test source; not itself tested."""
    return RegionSet(["AAA", "BBB", "AAA"], ["Alpha", "Beta", "Alpha islands"],
                     [[square(-10, -10, 20)], [square(60, 0, 10)], [square(100, -30, 4)]], "Test shapes", "PD", "r")


def setup(tmp_path, toml, files=None, width=360, frames=None):
    """Load a recipe, build context and layers, prepare them, and return (ctx, layers, renderer). Helper; not itself tested."""
    r = load_recipe(write_recipe(tmp_path / "r", toml, files))
    ctx = make_context(r, BuildOptions(width=width, frames=frames, build_dir=tmp_path / "b", workers=1))
    layers = make_layers(ctx)
    prepare_layers(ctx, layers)
    return ctx, layers, FrameRenderer(ctx, layers)


def test_sample_field_alignment():
    """Checks a hot grid cell lands at its longitude and latitude and a field spanning 360 degrees wraps without a seam. Covers sample_field; this test is not itself tested."""
    out = sample_field(_test_grid({}, None), 720, 360)
    y, x = np.unravel_index(np.argmax(out), out.shape)
    lon, lat = lon_lat_axes(720, 360)
    assert abs(math.degrees(lon[x]) - 10) < 0.6 and abs(math.degrees(lat[y]) - 65) < 0.6
    wave = GridField(np.tile(np.sin(np.radians(np.arange(360)))[None, :], (181, 1)).astype(np.float32), 0.0, 1.0, -90.0, 1.0)
    assert seam_ratio(sample_field(wave, 720, 360)) < 2.0


def test_grid_layer_glows_and_reports_times(tmp_path):
    """Checks the grid glows at the hot cell, reports its forecast time and peak, passes its check, and rejects unknown blends. Covers GridLayer prepare, render, values, checks and credits; this test is not itself tested."""
    toml = ('[dataset]\nname="g"\n[[layers]]\ntype="basemap"\ncolor="#000000"\n[[layers]]\ntype="grid"\n'
            'source={type="test_grid"}\ncolormap="#ffffff"\nopacity=0.5\nmin=0\nmax=80\n[hud]\nenabled=false\n')
    ctx, layers, fr = setup(tmp_path, toml)
    canvas = np.zeros((180, 360, 3), np.float32)
    layers[1].render(canvas, ctx.timeline.at(0))
    lon, lat = lon_lat_axes(360, 180)
    yi, xi = np.unravel_index(np.argmax(canvas.sum(-1)), (180, 360))
    assert abs(abs(np.degrees(lon[xi]) - 10) - 0.5) < 0.01 and abs(abs(np.degrees(lat[yi]) - 65) - 0.5) < 0.01
    assert canvas[yi, xi, 0] == pytest.approx(0.25 * 0.5, abs=2e-3) and canvas[100, 200].sum() == 0
    v = layers[1].values(ctx.timeline.at(0))
    assert v["forecast_time"].hour == 1 and v["grid_peak"] == 80
    assert layers[1].checks()[0].passed and layers[1].credits() == ["Test grid"]
    r = load_recipe(write_recipe(tmp_path / "bad", toml.replace('min=0', 'blend="mix"\nmin=0')))
    with pytest.raises(RecipeError, match="blend"):
        make_layers(make_context(r, BuildOptions(width=64, build_dir=tmp_path / "b2")))


@register_source("test_people")
def _test_people(opts, ctx):
    """Serve a 1-degree grid of people per km2 rising tenfold from west to east, 1 to 10,000, with a total. Test source; not itself tested."""
    v = np.zeros((180, 360), np.float32)
    for i, value in enumerate((1.0, 10.0, 100.0, 1000.0, 10000.0)):
        v[80:100, 100 + 30 * i:110 + 30 * i] = value
    return GridField(v, -179.5, 1.0, 89.5, -1.0, attribution="Test people", license="PD", fingerprint="p",
                     stats={"people": 8.2e9, "people_billions": 8.2})


def test_grid_log_scale_and_legend(tmp_path):
    """Checks a log-scale grid brightens by equal steps for each tenfold rise and leaves cells under its minimum dark, reports the source's numbers for HUD templates, draws its legend onto the frame at the HUD's anchors, and refuses an unknown scale and a minimum of 0 on a log scale. Covers GridLayer's scale, overlay and values with stats, and legend.Legend; the legend's look is checked by eye on where_people_live."""
    toml = ('[dataset]\nname="g"\n[[layers]]\ntype="basemap"\ncolor="#000000"\n[[layers]]\ntype="grid"\n'
            'source={type="test_people"}\ncolormap="#ffffff"\nscale="log"\nmin=1\nmax=10000\nlegend=true\n'
            'legend_title="People per km2"\n[hud]\nenabled=false\nanchors=[0]\n')
    ctx, layers, fr = setup(tmp_path, toml)
    canvas = np.zeros((180, 360, 3), np.float32)
    layers[1].render(canvas, ctx.timeline.at(0))
    lum = [float(canvas[90, 105 + 30 * i, 0]) for i in range(5)]
    assert lum[0] == pytest.approx(0, abs=1e-3) and lum[4] == pytest.approx(1, abs=1e-2)
    assert all(abs((lum[i + 1] - lum[i]) - 0.25) < 0.02 for i in range(4)), lum
    assert canvas[20, 20].sum() == 0
    v = layers[1].values(ctx.timeline.at(0))
    assert v["people_billions"] == 8.2 and v["grid_peak"] == 10000
    plain = np.zeros((180, 360, 3), np.uint8)
    framed = plain.copy()
    layers[1].overlay(framed, ctx.timeline.at(0))
    ys, xs = np.nonzero(framed.sum(-1))
    assert len(xs) and abs(xs.mean() - 180) < 20 and ys.mean() > 90 + 30, (xs.mean(), ys.mean())
    for bad, match in (('scale="log"', 'scale="ln"'), ("min=1", "min=0")):
        r = load_recipe(write_recipe(tmp_path / f"bad{len(match)}", toml.replace(bad, match)))
        with pytest.raises(RecipeError, match="scale must be|above 0"):
            make_layers(make_context(r, BuildOptions(width=64, build_dir=tmp_path / "b3")))


def test_daylight_follows_the_sun(tmp_path):
    """Checks the subsolar point is day and its antipode night at a fixed moment, values report the Sun, and bad times are refused. Covers DaylightLayer prepare, render, values and when; this test is not itself tested."""
    for d in (tmp_path / "r", tmp_path / "t" / "r"):
        d.mkdir(parents=True)
        cv2.imwrite(str(d / "day.png"), np.full((90, 180, 3), 255, np.uint8))
        cv2.imwrite(str(d / "night.png"), np.zeros((90, 180, 3), np.uint8))
    toml = ('[dataset]\nname="d"\n[[layers]]\ntype="daylight"\nday="day.png"\nnight="night.png"\n'
            'at="2024-06-20T12:00:00"\n[hud]\nenabled=false\n')
    ctx, layers, fr = setup(tmp_path, toml)
    t = ctx.timeline.at(0)
    canvas = np.zeros((180, 360, 3), np.float32)
    layers[0].render(canvas, t)
    v = layers[0].values(t)
    assert v["sun_lat"] == pytest.approx(23.44, abs=0.05)
    lon, lat = lon_lat_axes(360, 180)
    sx, sy = int(np.argmin(abs(np.degrees(lon) - v["sun_lon"]))), int(np.argmin(abs(np.degrees(lat) - v["sun_lat"])))
    ax = int(np.argmin(abs(np.degrees(lon) - (v["sun_lon"] + 180 if v["sun_lon"] < 0 else v["sun_lon"] - 180))))
    ay = int(np.argmin(abs(np.degrees(lat) + v["sun_lat"])))
    assert canvas[sy, sx].min() > 0.99 and canvas[ay, ax].max() < 0.01
    timeline = toml.replace('at="2024-06-20T12:00:00"\n', "")
    ctx2, l2, _ = setup(tmp_path / "t", timeline)
    with pytest.raises(RecipeError, match="start and end"):
        l2[0].render(np.zeros((180, 360, 3), np.float32), ctx2.timeline.at(0))
    r = load_recipe(write_recipe(tmp_path / "bad", toml.replace('at="2024-06-20T12:00:00"', 'at="noonish"')))
    with pytest.raises(RecipeError, match="ISO"):
        make_layers(make_context(r, BuildOptions(width=64, build_dir=tmp_path / "b3")))


def test_rasterise_regions_and_enclaves():
    """Checks regions fill their own pixels, a smaller enclave inside a larger region is drawn on top whatever the input order, and borders are drawn. Covers rasterise and ring_area_px; this test is not itself tested."""
    rs = RegionSet(["SMALL", "BIG"], ["s", "b"], [[square(-5, -5, 10)], [square(-20, -20, 40)]])
    ids, border = rasterise(rs, 360, 180)
    assert ids[90, 180] == 1 and ids[90, 168] == 2 and ids[10, 10] == 0
    assert border.max() > 0 and border[90, 100] == 0


def test_regions_shade_by_year_with_legend(tmp_path):
    """Checks log-scale values interpolate geometrically between data years, regions without data that year show as no-data, log colouring orders values, the legend is drawn, and ticks are round. Covers RegionsLayer prepare, norm, year_of, values_at, render, overlay, _legend, values, checks, credits and nice_ticks; this test is not itself tested."""
    toml = ('[dataset]\nname="r"\noutput="movie"\n[animation]\nframes=3\nloop=false\nstart="2000-01-01"\nend="2010-01-01"\n'
            '[[layers]]\ntype="basemap"\ncolor="#000000"\n[[layers]]\ntype="regions"\nregions={type="test_regions"}\n'
            'data={type="table",path="v.csv"}\nscale="log"\nmin=100\nmax=10000\nlegend_title="Things"\n'
            '[hud]\ntitle="{year}"\nanchors=[-120]\n')
    ctx, layers, fr = setup(tmp_path, toml, {"v.csv": "code,year,value\nAAA,2000,100\nAAA,2010,10000\nBBB,2005,1000\n"}, frames=3)
    reg = layers[1]
    assert reg.values_at(2005.0)[0] == pytest.approx(1000.0, rel=1e-4) and np.isnan(reg.values_at(2000.0)[1])
    assert np.isnan(reg.values_at(2011.5)[0]) and reg.values_at(2010.5)[0] == pytest.approx(10000.0)
    assert ctx.timeline.at(1).date.isoformat().startswith("2004-12-31T12")
    assert reg.values(ctx.timeline.at(1)) == {"year": 2004, "regions_with_data": 3}
    assert reg.values(ctx.timeline.at(0))["regions_with_data"] == 2 and reg.values_at(2005.0)[2] == reg.values_at(2005.0)[0]
    assert reg.norm(np.array([100.0, 1000.0, 10000.0])).tolist() == pytest.approx([0.0, 0.5, 1.0])
    c0, c2 = (np.zeros((180, 360, 3), np.float32) for _ in range(2))
    reg.render(c0, ctx.timeline.at(0))
    reg.render(c2, ctx.timeline.at(2))
    a = (90, int(lon_to_x(0, 360)))
    assert c2[a].sum() > c0[a].sum() > 0
    f = fr.frame(0)
    yl = int((90 + 46) / 180 * 180)
    assert f[yl - 3:yl + 3, int(lon_to_x(math.radians(-120), 360)) - 5:int(lon_to_x(math.radians(-120), 360)) + 5].max() > 0
    assert reg.checks()[0].passed and reg.credits() == ["Test shapes"]
    assert nice_ticks(500, 60000, True) == [500.0, 1000.0, 2000.0, 5000.0, 10000.0, 20000.0, 50000.0]
    assert nice_ticks(1, 1e6, True) == [1.0, 10.0, 100.0, 1000.0, 10000.0, 100000.0, 1000000.0]
    assert nice_ticks(0, 100, False) == [0, 20, 40, 60, 80, 100]
    r = load_recipe(write_recipe(tmp_path / "bad", toml.replace('scale="log"', 'scale="cube"')))
    with pytest.raises(RecipeError, match="scale"):
        make_layers(make_context(r, BuildOptions(width=64, frames=3, build_dir=tmp_path / "b4")))
