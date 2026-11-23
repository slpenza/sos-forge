"""Tests for DEM normals, horizon shadows and the shading model (ported from the lunar pipeline)."""
from __future__ import annotations

import math

import numpy as np
import pytest

from sosforge import grid, relief

R = 1_737_400.0


def test_normals_flat_and_slope():
    """Checks a flat DEM yields sphere normals and east/north-rising terrain tilts normals by the analytic slope. Covers surface_normals; this test is not itself tested."""
    w = 720
    lon, lat = grid.lon_lat_axes(w, w // 2)
    n = relief.surface_normals(np.zeros((w // 2, w), np.float32), R)
    up = np.stack([np.cos(lat)[:, None] * np.cos(lon)[None, :], np.cos(lat)[:, None] * np.sin(lon)[None, :],
                   np.broadcast_to(np.sin(lat)[:, None], (w // 2, w))])
    assert np.allclose(n, up, atol=1e-6)
    y, x = w // 4, w // 2
    east = np.array([-math.sin(lon[x]), math.cos(lon[x]), 0.0])
    north = np.array([-math.sin(lat[y]) * math.cos(lon[x]), -math.sin(lat[y]) * math.sin(lon[x]), math.cos(lat[y])])
    ne = relief.surface_normals(np.broadcast_to((0.1 * R * np.sin(lon))[None, :], (w // 2, w)).astype(np.float32), R)
    assert ne[:, y, x] @ east == pytest.approx(-0.1 * math.cos(lon[x]) / math.sqrt(1 + 0.01 * math.cos(lon[x]) ** 2), abs=1e-4)
    nn = relief.surface_normals(np.broadcast_to((0.1 * R * lat)[:, None], (w // 2, w)).astype(np.float32), R)
    assert nn[:, y, x] @ north == pytest.approx(-0.1 / math.sqrt(1.01), abs=1e-4)


def test_horizon_flat_and_ridge():
    """Checks a flat body has a tiny negative horizon and a 5 km wall 1-2 deg east shadows a low eastern Sun only. Covers horizon_distances, horizon_map, bearings and sun_visibility; this test is not itself tested."""
    w = 360
    lon, lat = grid.lon_lat_axes(w, w // 2)
    d = relief.horizon_distances(w, R)
    flat = np.zeros((w // 2, w), np.float32)
    assert np.all(relief.horizon_map(flat, math.pi / 2, d, R).astype(np.float32) < 0)
    y, x = w // 4, int(np.argmin(np.abs(lon - np.radians(0.5))))
    wall = flat.copy()
    wall[:, x + 1:x + 3] = 5000.0
    sw = np.stack([relief.horizon_map(wall, b, d, R) for b in relief.bearings()])
    sf = np.stack([relief.horizon_map(flat, b, d, R) for b in relief.bearings()])
    assert sw[2, y, x] > 0.03 and abs(float(sw[6, y, x]) - float(sf[6, y, x])) < 1e-3
    sun = math.radians(0.5 + 87.0)
    assert relief.sun_visibility(sf[:, y:y + 1], lat[y:y + 1], lon, sun)[0, x] > 0.99
    assert relief.sun_visibility(sw[:, y:y + 1], lat[y:y + 1], lon, sun)[0, x] < 0.01


def test_shading_lambert_ambient_and_brdf():
    """Checks night pixels equal the ambient floor, the subsolar point is 1, Lommel-Seeliger normalises, and bad BRDFs raise. Covers shading_rows and sun_vector; this test is not itself tested."""
    w = 128
    lon, lat = grid.lon_lat_axes(w, w // 2)
    n = relief.surface_normals(np.zeros((w // 2, w), np.float32), R)
    s = relief.shading_rows(n, None, lat, lon, 0, w // 2, 0.0, ambient=0.05)
    assert s[:, 0].max() == pytest.approx(0.05) and s[w // 4 - 1:w // 4 + 1, w // 2].mean() == pytest.approx(1.0, abs=2e-3)
    ls = relief.shading_rows(n, None, lat, lon, 0, w // 2, 0.0, ambient=0.0, brdf="lommel-seeliger")
    x60 = int(np.argmin(np.abs(lon - math.radians(60))))
    mu0 = math.cos(lon[x60]) * math.cos(lat[w // 4])
    assert ls[w // 4, x60] == pytest.approx(2 * mu0 / (1 + mu0), abs=1e-4)
    assert np.linalg.norm(relief.sun_vector(0.3, 0.1)) == pytest.approx(1.0)
    with pytest.raises(ValueError):
        relief.shading_rows(n, None, lat, lon, 0, 1, 0.0, brdf="phong")
