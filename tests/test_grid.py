"""Tests for grid conventions, colour encoding, resampling and spherical helpers."""
from __future__ import annotations

import math

import numpy as np
import pytest

from sosforge import grid


def test_axes_prime_meridian_and_wrap():
    """Checks lon 0 sits at x = W/2 and the +/-180 wrap spacing equals the interior spacing. Covers lon_lat_axes and lon_to_x; this test is not itself tested."""
    w = 4096
    lon, lat = grid.lon_lat_axes(w, w // 2)
    assert grid.lon_to_x(0.0, w) == pytest.approx(w / 2)
    assert lon[w // 2 - 1] < 0 < lon[w // 2] and lon[w // 2] == pytest.approx(-lon[w // 2 - 1])
    assert (lon[0] + 2 * np.pi) - lon[-1] == pytest.approx(lon[1] - lon[0])
    assert lat[0] == pytest.approx(-lat[-1])


def test_latlon_to_px_round_trip():
    """Checks degree coordinates land on the matching pixel centres, including wrap at +180. Covers latlon_to_px; this test is not itself tested."""
    w, h = 360, 180
    lon, lat = grid.lon_lat_axes(w, h)
    x, y = grid.latlon_to_px(np.degrees(lat[[0, 90]]), np.degrees(lon[[0, 200]]), w, h)
    assert np.allclose(x, [0, 200]) and np.allclose(y, [0, 90])
    x180, _ = grid.latlon_to_px(np.array([0.0]), np.array([180.0]), w, h)
    assert x180[0] == pytest.approx(-0.5)


def test_srgb_round_trip():
    """Checks sRGB decode/encode invert each other and the 8-bit LUT matches the analytic encoder. Covers srgb_to_linear, linear_to_srgb and linear_to_srgb8; this test is not itself tested."""
    x = np.linspace(0, 1, 1001, dtype=np.float32)
    assert np.allclose(grid.linear_to_srgb(grid.srgb_to_linear(x)), x, atol=1e-5)
    assert np.abs(grid.linear_to_srgb8(x).astype(int) - np.round(grid.linear_to_srgb(x) * 255)).max() <= 1


def test_seam_ratio_and_luminance():
    """Checks a periodic image has seam ratio near 1, a step at the edge is flagged, and luminance weights sum to 1. Covers seam_ratio and luminance; this test is not itself tested."""
    lon, _ = grid.lon_lat_axes(256, 128)
    periodic = np.broadcast_to(np.sin(lon)[None, :], (128, 256))
    assert grid.seam_ratio(periodic) == pytest.approx(math.pi / 2, rel=0.01)
    assert grid.seam_ratio(np.broadcast_to(lon[None, :], (128, 256))) > 10
    assert grid.luminance(np.ones(3)) == pytest.approx(1.0)


def test_parse_color():
    """Checks hex and list colours decode to linear light and malformed values raise. Covers parse_color; this test is not itself tested."""
    assert np.allclose(grid.parse_color("#ffffff"), 1.0) and np.allclose(grid.parse_color([0, 0, 0]), 0.0)
    assert grid.parse_color("#808080")[0] == pytest.approx(grid.srgb_to_linear(np.float32(128 / 255)), abs=1e-6)
    with pytest.raises(ValueError):
        grid.parse_color("#fff")


@pytest.mark.parametrize("dst", [128, 720])
def test_resample_alignment_and_seam(dst):
    """Checks shrinking and enlarging keep a +30 deg feature in place and a periodic map seamless. Covers resample_equirect; this test is not itself tested."""
    lon, _ = grid.lon_lat_axes(360, 180)
    feat = np.exp(-((np.degrees(lon) - 30) / 6) ** 2)
    out = grid.resample_equirect(np.broadcast_to(feat[None, :], (180, 360)).astype(np.float32), dst, dst // 2)
    lon_d, _ = grid.lon_lat_axes(dst, dst // 2)
    assert abs(np.degrees(lon_d[np.argmax(out[dst // 4])]) - 30) <= 360 / dst + 1
    assert grid.seam_ratio(out) < 1.5


def test_great_circle():
    """Checks haversine distance on known cases and that unit vectors round-trip through lat/lon. Covers great_circle_m, unit_vectors and vectors_to_latlon; this test is not itself tested."""
    assert grid.great_circle_m(0, 0, 0, 1) == pytest.approx(2 * math.pi * grid.EARTH_RADIUS_M / 360, rel=1e-9)
    assert grid.great_circle_m(0, 179.5, 0, -179.5) == pytest.approx(2 * math.pi * grid.EARTH_RADIUS_M / 360, rel=1e-9)
    la, lo = grid.vectors_to_latlon(grid.unit_vectors(np.array([12.5, -40.0]), np.array([170.0, -60.0])))
    assert np.allclose(la, [12.5, -40.0]) and np.allclose(lo, [170.0, -60.0])


def test_cap_mean():
    """Checks the annulus mean picks values only inside the requested ring. Covers cap_mean; this test is not itself tested."""
    lon, lat = grid.lon_lat_axes(360, 180)
    img = np.where(np.abs(np.degrees(lat))[:, None] < 5, 1.0, 0.0) * np.ones((1, 360))
    assert grid.cap_mean(img, 0, 0, 0, 4) == pytest.approx(1.0)
    assert grid.cap_mean(img, 60, 0, 0, 10) == pytest.approx(0.0)
