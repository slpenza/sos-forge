"""Planetary relief lighting: DEM surface normals, great-circle horizon maps for cast shadows, and a sunlit
shading model.  Body-agnostic: pass the body's radius (Moon 1737.4 km, Mars 3389.5 km, Earth 6371 km)."""
from __future__ import annotations

import math

import cv2
import numpy as np

from .grid import lon_lat_axes

SUN_ANGULAR_RADIUS = math.radians(0.2666)
N_BEARINGS = 8


def surface_normals(height_m: np.ndarray, radius_m: float, relief: float = 1.0) -> np.ndarray:
    """Build body-frame unit normals (3, H, W) by tilting each sphere normal with east/north DEM slopes, wrapping in longitude. Tested by test_relief.py::test_normals_flat_and_slope."""
    h_px, w_px = height_m.shape
    lon, lat = lon_lat_axes(w_px, h_px)
    h = height_m.astype(np.float64) * relief
    dh_dlon = (np.roll(h, -1, axis=1) - np.roll(h, 1, axis=1)) / (2.0 * (2.0 * np.pi / w_px))
    dh_dlat = -np.gradient(h, axis=0) / (np.pi / h_px)
    g_east = dh_dlon / (radius_m * np.maximum(np.cos(lat), 1e-3)[:, None])
    g_north = dh_dlat / radius_m
    inv = 1.0 / np.sqrt(1.0 + g_east ** 2 + g_north ** 2)
    n_e, n_n, n_u = -g_east * inv, -g_north * inv, inv
    sl, cl = np.sin(lon)[None, :], np.cos(lon)[None, :]
    sp, cp = np.sin(lat)[:, None], np.cos(lat)[:, None]
    return np.stack([-n_e * sl - n_n * sp * cl + n_u * cp * cl,
                     n_e * cl - n_n * sp * sl + n_u * cp * sl,
                     n_n * cp + n_u * sp]).astype(np.float32)


def horizon_distances(width: int, radius_m: float, max_km: float = 270.0, ratio: float = 1.12) -> np.ndarray:
    """Return geometrically spaced ray-march distances in metres from sub-pixel out to max_km. Tested by test_relief.py::test_horizon_flat_and_ridge."""
    px = 2.0 * np.pi * radius_m / width
    out, d = [], 0.75 * px
    while d <= max_km * 1000.0:
        out.append(d)
        d = max(d * ratio, d + 0.5 * px)
    return np.asarray(out, dtype=np.float64)


def horizon_map(height_m: np.ndarray, bearing: float, distances: np.ndarray, radius_m: float) -> np.ndarray:
    """Return sin(horizon elevation) along one great-circle bearing for every pixel, including body curvature. Tested by test_relief.py::test_horizon_flat_and_ridge."""
    h_px, w_px = height_m.shape
    _, lat = lon_lat_axes(w_px, h_px)
    h = np.ascontiguousarray(height_m, dtype=np.float32)
    padded = cv2.copyMakeBorder(h, 1, 1, 0, 0, cv2.BORDER_REPLICATE)
    sp, cp = np.sin(lat), np.cos(lat)
    xs = np.arange(w_px, dtype=np.float64)[None, :]
    best = np.full((h_px, w_px), -1.0, dtype=np.float32)
    for d in distances:
        ang = d / radius_m
        sp2 = np.clip(sp * math.cos(ang) + cp * math.sin(ang) * math.cos(bearing), -1.0, 1.0)
        dlon = np.arctan2(math.sin(bearing) * math.sin(ang) * cp, math.cos(ang) - sp * sp2)
        map_x = np.mod(xs + (dlon / (2.0 * np.pi) * w_px)[:, None], w_px).astype(np.float32)
        y2 = np.clip((np.pi / 2.0 - np.arcsin(sp2)) / np.pi * h_px - 0.5, -0.5, h_px - 0.5) + 1.0
        map_y = np.ascontiguousarray(np.broadcast_to(y2[:, None], (h_px, w_px)), dtype=np.float32)
        h2 = cv2.remap(padded, map_x, map_y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_WRAP)
        r2 = radius_m + h2
        vert = (h2 - h) - r2 * np.float32(2.0 * math.sin(ang / 2.0) ** 2)
        horiz = r2 * np.float32(math.sin(ang))
        np.maximum(best, vert / np.sqrt(horiz * horiz + vert * vert), out=best)
    return best.astype(np.float16)


def bearings() -> np.ndarray:
    """Return the horizon-map bearings in radians clockwise from north. Tested indirectly by test_relief.py::test_horizon_flat_and_ridge."""
    return np.arange(N_BEARINGS) * (2.0 * np.pi / N_BEARINGS)


def sun_vector(sun_lon: float, sun_lat: float = 0.0) -> np.ndarray:
    """Return the unit body-frame vector toward the Sun for a subsolar longitude and latitude in radians. Tested by test_relief.py::test_shading_lambert_ambient_and_brdf."""
    return np.array([math.cos(sun_lat) * math.cos(sun_lon), math.cos(sun_lat) * math.sin(sun_lon), math.sin(sun_lat)])


def sun_visibility(horizons: np.ndarray, lat: np.ndarray, lon: np.ndarray, sun_lon: float,
                   sun_lat: float = 0.0) -> np.ndarray:
    """Return the visible fraction of the solar disc per pixel from sun elevation versus the bearing-interpolated horizon. Tested by test_relief.py::test_horizon_flat_and_ridge."""
    dl = (lon - sun_lon).astype(np.float32)
    cs, ss = np.float32(math.cos(sun_lat)), np.float32(math.sin(sun_lat))
    cdl, sdl = (cs * np.cos(dl))[None, :], (cs * np.sin(dl))[None, :]
    sp, cp = np.sin(lat).astype(np.float32)[:, None], np.cos(lat).astype(np.float32)[:, None]
    sin_el = cp * cdl + sp * ss
    bearing = np.arctan2(-sdl, cp * ss - sp * cdl)
    f = np.mod(bearing * np.float32(N_BEARINGS / (2.0 * np.pi)), N_BEARINGS)
    i0 = np.floor(f).astype(np.intp)
    w = f - i0
    i0 %= N_BEARINGS
    hz0 = np.take_along_axis(horizons, i0[None], axis=0)[0].astype(np.float32)
    hz1 = np.take_along_axis(horizons, ((i0 + 1) % N_BEARINGS)[None], axis=0)[0].astype(np.float32)
    sin_hz = hz0 + w * (hz1 - hz0)
    return np.clip((sin_el - sin_hz) * np.float32(0.5 / SUN_ANGULAR_RADIUS) + 0.5, 0.0, 1.0)


def shading_rows(normals: np.ndarray, horizons: np.ndarray | None, lat: np.ndarray, lon: np.ndarray, y0: int,
                 y1: int, sun_lon: float, sun_lat: float = 0.0, ambient: float = 0.025,
                 brdf: str = "lambert") -> np.ndarray:
    """Return ambient + (1 - ambient) * max(0, N.S) * visibility for rows y0..y1, with an optional Lommel-Seeliger law. Tested by test_relief.py::test_shading_lambert_ambient_and_brdf."""
    sx, sy, sz = (np.float32(c) for c in sun_vector(sun_lon, sun_lat))
    n = normals[:, y0:y1]
    mu = n[0] * sx + n[1] * sy
    if sz != 0.0:
        mu += n[2] * sz
    np.maximum(mu, 0.0, out=mu)
    if brdf == "lommel-seeliger":
        mu = 2.0 * mu / (1.0 + mu)
    elif brdf != "lambert":
        raise ValueError(f"unknown brdf {brdf!r} (use 'lambert' or 'lommel-seeliger')")
    if horizons is not None:
        mu *= sun_visibility(horizons[:, y0:y1], lat[y0:y1], lon, sun_lon, sun_lat)
    return np.float32(ambient) + np.float32(1.0 - ambient) * mu
