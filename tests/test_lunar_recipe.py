"""Tests for the bundled lunar_phases recipe hooks and, when the SVS maps are cached, a real build."""
from __future__ import annotations

import math
import shutil

import numpy as np
import pytest

from conftest import REAL_CACHE, REPO
from sosforge.build import BuildOptions, build
from sosforge.recipe import load_recipe
from sosforge.registry import ICONS
from sosforge.timeline import Timeline

SVS = [REAL_CACHE / "assets" / f for f in ("lroc_color_poles_4k.tif", "ldem_16.tif")]
needs_svs = pytest.mark.skipif(not all(p.exists() for p in SVS), reason="SVS Moon Kit maps not cached")


@pytest.fixture()
def lunar():
    """Load the bundled lunar recipe and return its hook module. Fixture; not itself tested."""
    return load_recipe(REPO / "recipes" / "lunar_phases").hooks


@pytest.mark.parametrize("day,name", [(0, "New Moon"), (0.9, "New Moon"), (2, "Waxing Crescent"), (7.38, "First Quarter"),
                                      (10, "Waxing Gibbous"), (14.77, "Full Moon"), (18, "Waning Gibbous"),
                                      (22.15, "Third Quarter"), (26, "Waning Crescent"), (29.4, "New Moon")])
def test_values_and_phase_names(lunar, day, name):
    """Checks phase labels across the cycle, the lit fraction, and that values() maps the loop fraction to days. Covers phase_name, illuminated_fraction and values; this test is not itself tested."""
    assert lunar.phase_name(day) == name
    assert lunar.illuminated_fraction(lunar.SYNODIC_DAYS / 2) == pytest.approx(1)
    v = lunar.values(Timeline(720, 30, True).at(719), None)
    assert f"{v['day']:.1f}" == "29.5" and v["phase"] == "New Moon"


def test_moon_icon_waxing_lit_on_right(lunar):
    """Checks the Earth-view icon is lit on the right while waxing and on the left while waning. Covers moon_icon via the icon registry; this test is not itself tested."""
    icon = ICONS["moon_phase"]
    for sun_lon, right in ((131.0, True), (-131.0, False)):
        g = np.asarray(icon(84, {"sun_lon_deg": sun_lon}), np.float32)
        lum = g[..., 0] * g[..., 3] / 255
        assert bool(lum[:, 42:].mean() > lum[:, :42].mean()) is right


@needs_svs
def test_landmark_checks_on_real_maps(lunar):
    """Checks the landmark checks pass on the real SVS maps and fail when rotated, mirrored or misregistered. Covers landmark_checks thresholds; skipped without the SVS files."""
    from sosforge.basemaps import load_rgb
    from sosforge.grid import resample_equirect, srgb_to_linear
    from sosforge.layers.lit_globe import load_elevation
    w = 1024
    alb = resample_equirect(srgb_to_linear(load_rgb(SVS[0])), w, w // 2)
    h = resample_equirect(load_elevation(SVS[1], "km", None, 0.0), w, w // 2)
    assert all(c.passed for c in lunar.landmark_checks(alb, h))
    assert not lunar.landmark_checks(np.roll(alb, w // 2, 1), np.roll(h, w // 2, 1))[0].passed
    mir = lunar.landmark_checks(alb[:, ::-1].copy(), h[:, ::-1].copy())
    assert not mir[1].passed and not mir[2].passed
    assert not lunar.landmark_checks(alb, np.roll(h, w // 2, 1))[3].passed


@needs_svs
def test_lunar_preview_build(tmp_path):
    """Checks the real lunar recipe builds a small movie whose every check passes, including landmarks and video. Covers LitGlobeLayer.prepare/render with real maps and the recipe hooks; skipped without the SVS files."""
    (tmp_path / "cache" / "assets").mkdir(parents=True)
    for p in SVS:
        shutil.copy2(p, tmp_path / "cache" / "assets" / p.name)
    s = build(REPO / "recipes" / "lunar_phases", BuildOptions(width=256, frames=8, workers=2, preset="ultrafast",
                                                              out_dir=tmp_path / "dist", build_dir=tmp_path / "b"))
    assert all(c["passed"] for c in s["checks"]), [c for c in s["checks"] if not c["passed"]]
    assert s["video"]["frames"] == 8 and math.isclose(s["video"]["fps"], 30)
