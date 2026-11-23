"""Equirectangular conventions, colour encoding and seam-safe resampling shared by every layer.

Pixel (x, y) centre -> lon = ((x + 0.5) / W) * 2pi - pi (0 deg at x = W/2), lat = pi/2 - ((y + 0.5) / H) * pi.
Body frame: +X toward (lat 0, lon 0), +Y toward lon +90 (east), +Z north.  Lighting is linear light.
"""
from __future__ import annotations

import math

import cv2
import numpy as np

EARTH_RADIUS_M = 6_371_008.8
REC709 = np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)


def lon_lat_axes(width: int, height: int) -> tuple[np.ndarray, np.ndarray]:
    """Return pixel-centre longitude (W,) and latitude (H,) vectors in radians for a 2:1 grid. Tested by test_grid.py::test_axes_prime_meridian_and_wrap."""
    lon = ((np.arange(width, dtype=np.float64) + 0.5) / width) * 2.0 * np.pi - np.pi
    lat = np.pi / 2.0 - ((np.arange(height, dtype=np.float64) + 0.5) / height) * np.pi
    return lon, lat


def lon_to_x(lon_rad: float | np.ndarray, width: int) -> float | np.ndarray:
    """Convert longitude in radians to a continuous pixel-edge x coordinate where lon 0 is width/2. Tested by test_grid.py::test_axes_prime_meridian_and_wrap."""
    return (np.asarray(lon_rad) + np.pi) / (2.0 * np.pi) * width


def latlon_to_px(lat_deg: np.ndarray, lon_deg: np.ndarray, width: int, height: int) -> tuple[np.ndarray, np.ndarray]:
    """Convert degrees to continuous pixel-centre coordinates so pixel i spans i - 0.5 to i + 0.5. Tested by test_grid.py::test_latlon_to_px_round_trip."""
    lon = (np.asarray(lon_deg, dtype=np.float64) + 180.0) % 360.0
    x = lon / 360.0 * width - 0.5
    y = (90.0 - np.asarray(lat_deg, dtype=np.float64)) / 180.0 * height - 0.5
    return x, y


def srgb_to_linear(v: np.ndarray) -> np.ndarray:
    """Decode sRGB values in [0, 1] to linear light as float32. Tested by test_grid.py::test_srgb_round_trip."""
    v = np.asarray(v, dtype=np.float32)
    return np.where(v <= 0.04045, v / 12.92, ((v + 0.055) / 1.055) ** 2.4).astype(np.float32)


def linear_to_srgb(v: np.ndarray) -> np.ndarray:
    """Encode linear light in [0, 1] to sRGB as float32. Tested by test_grid.py::test_srgb_round_trip."""
    v = np.clip(np.asarray(v, dtype=np.float32), 0.0, 1.0)
    return np.where(v <= 0.0031308, v * 12.92, 1.055 * v ** (1.0 / 2.4) - 0.055).astype(np.float32)


_SRGB8_LUT: np.ndarray | None = None


def linear_to_srgb8(v: np.ndarray) -> np.ndarray:
    """Encode linear light to 8-bit sRGB through a 65536-entry lookup table. Tested by test_grid.py::test_srgb_round_trip."""
    global _SRGB8_LUT
    if _SRGB8_LUT is None:
        _SRGB8_LUT = np.round(linear_to_srgb(np.linspace(0.0, 1.0, 65536, dtype=np.float32)) * 255).astype(np.uint8)
    return _SRGB8_LUT[(np.clip(v, 0.0, 1.0) * 65535.0 + 0.5).astype(np.uint16)]


def luminance(rgb: np.ndarray) -> np.ndarray:
    """Return Rec.709 relative luminance of an (..., 3) array. Tested by test_grid.py::test_seam_ratio_and_luminance."""
    return np.asarray(rgb, dtype=np.float32) @ REC709


def parse_color(color) -> np.ndarray:
    """Turn '#rrggbb' or an [r, g, b] 0-255 list into a linear-light float32 RGB triple. Tested by test_grid.py::test_parse_color."""
    if isinstance(color, str):
        s = color.lstrip("#")
        if len(s) != 6:
            raise ValueError(f"colour {color!r} must look like '#rrggbb'")
        rgb = [int(s[i:i + 2], 16) for i in (0, 2, 4)]
    else:
        rgb = list(color)
        if len(rgb) != 3:
            raise ValueError(f"colour {color!r} must have three components")
    return srgb_to_linear(np.asarray(rgb, dtype=np.float32) / 255.0)


def resample_equirect(src: np.ndarray, width: int, height: int) -> np.ndarray:
    """Resample an equirectangular raster, area-averaging when shrinking and wrap-aware bilinear when enlarging. Tested by test_grid.py::test_resample_alignment_and_seam."""
    src = np.ascontiguousarray(src, dtype=np.float32)
    h0, w0 = src.shape[:2]
    if (w0, h0) == (width, height):
        return src.copy()
    if width <= w0 and height <= h0:
        return cv2.resize(src, (width, height), interpolation=cv2.INTER_AREA)
    xs = ((np.arange(width) + 0.5) * (w0 / width) - 0.5) % w0
    ys = np.clip((np.arange(height) + 0.5) * (h0 / height) - 0.5, -0.5, h0 - 0.5) + 1.0
    padded = cv2.copyMakeBorder(src, 1, 1, 0, 0, cv2.BORDER_REPLICATE)
    map_x = np.ascontiguousarray(np.broadcast_to(xs[None, :], (height, width)), dtype=np.float32)
    map_y = np.ascontiguousarray(np.broadcast_to(ys[:, None], (height, width)), dtype=np.float32)
    return cv2.remap(padded, map_x, map_y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_WRAP)


def seam_ratio(img: np.ndarray) -> float:
    """Return mean left/right edge difference over mean adjacent-column difference, where about 1 means no seam. Tested by test_grid.py::test_seam_ratio_and_luminance."""
    a = np.asarray(img, dtype=np.float32)
    seam = np.abs(a[:, 0] - a[:, -1]).mean()
    interior = np.abs(np.diff(a, axis=1)).mean()
    return float(seam / max(interior, 1e-12))


def great_circle_m(lat1, lon1, lat2, lon2, radius: float = EARTH_RADIUS_M) -> np.ndarray:
    """Return haversine distances in metres between degree coordinates. Tested by test_grid.py::test_great_circle."""
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dp, dl = p2 - p1, np.radians(np.asarray(lon2) - np.asarray(lon1))
    a = np.sin(dp / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return 2.0 * radius * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))


def unit_vectors(lat_deg: np.ndarray, lon_deg: np.ndarray) -> np.ndarray:
    """Return (N, 3) body-frame unit vectors for degree coordinates. Tested by test_grid.py::test_great_circle."""
    la, lo = np.radians(lat_deg), np.radians(lon_deg)
    return np.stack([np.cos(la) * np.cos(lo), np.cos(la) * np.sin(lo), np.sin(la)], axis=-1)


def vectors_to_latlon(v: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Convert (N, 3) vectors to degree latitude and longitude. Tested by test_grid.py::test_great_circle."""
    v = v / np.linalg.norm(v, axis=-1, keepdims=True)
    return np.degrees(np.arcsin(np.clip(v[..., 2], -1, 1))), np.degrees(np.arctan2(v[..., 1], v[..., 0]))


def angular_radius_px(radius_m: float, width: int, body_radius_m: float) -> float:
    """Return how many meridional pixels a surface distance spans at a given output width. Tested by test_density.py::test_glow_same_angular_size_at_any_latitude."""
    return radius_m / (body_radius_m * math.pi / (width // 2))


def cap_mean(img: np.ndarray, lat0_deg: float, lon0_deg: float, r0_deg: float, r1_deg: float) -> float:
    """Return the mean of a map inside the spherical annulus r0..r1 degrees around a point. Tested by test_grid.py::test_cap_mean."""
    h_px, w_px = img.shape[:2]
    lon, lat = lon_lat_axes(w_px, h_px)
    la0, lo0 = math.radians(lat0_deg), math.radians(lon0_deg)
    cosd = np.sin(lat)[:, None] * math.sin(la0) + np.cos(lat)[:, None] * math.cos(la0) * np.cos(lon[None, :] - lo0)
    dist = np.degrees(np.arccos(np.clip(cosd, -1.0, 1.0)))
    return float(img[(dist >= r0_deg) & (dist < r1_deg)].mean())
