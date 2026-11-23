"""Tests for point deposit and the latitude-correct spherical Gaussian."""
from __future__ import annotations

import numpy as np
import pytest

from sosforge import density, grid

R = grid.EARTH_RADIUS_M


def test_deposit_conserves_weight_and_wraps():
    """Checks bilinear deposit conserves total weight and splits a +180 point across the seam columns. Covers deposit; this test is not itself tested."""
    d = density.deposit(np.array([10.0, -33.3, 0.0]), np.array([20.0, 151.2, 180.0]), np.array([1.0, 2.0, 3.0]), 360, 180)
    assert d.sum() == pytest.approx(6.0, rel=1e-6)
    seam = density.deposit(np.array([0.5]), np.array([180.0]), None, 360, 180)
    assert seam[:, 0].sum() == pytest.approx(0.5) and seam[:, -1].sum() == pytest.approx(0.5)
    assert density.deposit(np.array([]), np.array([]), None, 8, 4).sum() == 0


def _ground_radius_km(img: np.ndarray, lat_deg: float, lon_deg: float, level: float) -> tuple[float, float]:
    """Return the east-west and north-south ground extent in km where a blob exceeds a level. Helper; not itself tested."""
    h, w = img.shape
    lon, lat = grid.lon_lat_axes(w, h)
    y = int(np.argmin(np.abs(lat - np.radians(lat_deg))))
    x = int(np.argmin(np.abs(lon - np.radians(lon_deg))))
    ew = (img[y] > level).sum() * (2 * np.pi * R / w) * np.cos(lat[y]) / 1000
    ns = (img[:, x] > level).sum() * (np.pi * R / h) / 1000
    return ew, ns


def test_glow_same_angular_size_at_any_latitude():
    """Checks a peak-normalised glow reads ~1 at its centre and is round on the ground at 0 and 60 degrees latitude. Covers spherical_gaussian, glow and angular_radius_px; this test is not itself tested."""
    w, h = 1440, 720
    for la in (0.0, 60.0):
        d = density.deposit(np.array([la + 0.125]), np.array([0.125]), None, w, h)
        g = density.spherical_gaussian(d, 300_000, R)
        assert g.max() == pytest.approx(1.0, abs=0.08)
        ew, ns = _ground_radius_km(g, la + 0.125, 0.125, 0.5)
        assert ew == pytest.approx(ns, rel=0.2), (la, ew, ns)
    assert 0 < density.glow(np.array([1.0]), 0.9)[0] < 1
    assert grid.angular_radius_px(10_000, 4096, R) == pytest.approx(10_000 / (R * np.pi / 2048))


def test_blur_is_seamless():
    """Checks a point at +179.9 deg blurs continuously across the left/right edge and a polar point fills its ring. Covers spherical_gaussian wrap and polar paths; this test is not itself tested."""
    d = density.deposit(np.array([0.0, 89.9]), np.array([179.9, 0.0]), None, 720, 360)
    g = density.spherical_gaussian(d, 200_000, R)
    assert g[180, 0] > 0.3 and g[180, -1] > 0.3
    assert g[0].min() > 0.5 * g[0].max()
