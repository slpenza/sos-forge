"""Flat panels placed on the sphere: draw a rectangle flat, then warp it into the equirectangular frame
through an inverse gnomonic projection so it reads undistorted on the globe at any latitude."""
from __future__ import annotations

import math
from dataclasses import dataclass

import cv2
import numpy as np

from .grid import lon_to_x


def inverse_gnomonic(x: np.ndarray, y: np.ndarray, lat0: float) -> tuple[np.ndarray, np.ndarray]:
    """Map tangent-plane coordinates around (lat0, lon 0) to latitude and longitude offset in radians. Tested by test_panels.py::test_gnomonic_round_trip."""
    rho = np.hypot(x, y)
    c = np.arctan(rho)
    sin_c, cos_c = np.sin(c), np.cos(c)
    safe = np.where(rho > 0, rho, 1.0)
    lat = np.arcsin(np.clip(cos_c * math.sin(lat0) + np.where(rho > 0, y * sin_c * math.cos(lat0) / safe, 0.0), -1, 1))
    dlon = np.arctan2(x * sin_c, rho * math.cos(lat0) * cos_c - y * math.sin(lat0) * sin_c)
    return lat, dlon


def forward_gnomonic(lat: np.ndarray, dlon: np.ndarray, lat0: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Project latitude and longitude offset onto the tangent plane at (lat0, lon 0), returning x, y and cos of the angular distance. Tested by test_panels.py::test_gnomonic_round_trip."""
    cos_c = math.sin(lat0) * np.sin(lat) + math.cos(lat0) * np.cos(lat) * np.cos(dlon)
    safe = np.where(cos_c > 1e-6, cos_c, 1e-6)
    x = np.cos(lat) * np.sin(dlon) / safe
    y = (math.cos(lat0) * np.sin(lat) - math.sin(lat0) * np.cos(lat) * np.cos(dlon)) / safe
    return x, y, cos_c


def panel_pixels(width: int, lat_deg: float, width_deg: float, height_deg: float,
                 oversample: float = 1.25) -> tuple[int, int]:
    """Return the panel raster size that matches the frame's pixel density at the panel's latitude. Tested by test_panels.py::test_placement_centred_and_scaled."""
    density = width / (2.0 * math.pi) / max(math.cos(math.radians(lat_deg)), 0.2) * oversample
    return (max(16, int(round(2.0 * math.tan(math.radians(width_deg / 2.0)) * density))),
            max(8, int(round(2.0 * math.tan(math.radians(height_deg / 2.0)) * density))))


@dataclass
class Placement:
    """Precomputed warp of a flat panel into one equirectangular window with wrapped columns. Tested by test_panels.py::test_placement_centred_and_scaled."""

    row0: int
    row1: int
    cols: np.ndarray
    map_u: np.ndarray
    map_v: np.ndarray

    @classmethod
    def build(cls, width: int, height: int, lon_deg: float, lat_deg: float, width_deg: float, height_deg: float,
              panel_w: int, panel_h: int) -> "Placement":
        """Compute the frame window and inverse-gnomonic sample maps for a panel centred at (lon, lat). Tested by test_panels.py::test_placement_centred_and_scaled and test_placement_wraps_seam."""
        lat0, lon0 = math.radians(lat_deg), math.radians(lon_deg)
        tx, ty = math.tan(math.radians(width_deg / 2.0)), math.tan(math.radians(height_deg / 2.0))
        t = np.linspace(-1.0, 1.0, 129)
        bx = np.concatenate([t * tx, t * tx, np.full_like(t, -tx), np.full_like(t, tx)])
        by = np.concatenate([np.full_like(t, -ty), np.full_like(t, ty), t * ty, t * ty])
        lat_b, dlon_b = inverse_gnomonic(bx, by, lat0)
        yb = (np.pi / 2.0 - lat_b) / np.pi * height
        row0, row1 = max(0, int(math.floor(yb.min())) - 2), min(height, int(math.ceil(yb.max())) + 2)
        if lat_b.max() > math.radians(89) or lat_b.min() < math.radians(-89):
            col0, col1 = 0, width
        else:
            xb = lon_to_x(lon0 + dlon_b, width)
            col0, col1 = int(math.floor(xb.min())) - 2, int(math.ceil(xb.max())) + 2
            col1 = min(col1, col0 + width)
        cols = np.arange(col0, col1)
        lon = ((cols + 0.5) / width) * 2.0 * np.pi - np.pi
        lat = np.pi / 2.0 - ((np.arange(row0, row1) + 0.5) / height) * np.pi
        dlon = (lon[None, :] - lon0 + np.pi) % (2.0 * np.pi) - np.pi
        x, y, cos_c = forward_gnomonic(lat[:, None], dlon, lat0)
        map_u = np.where(cos_c > 0.0, (x / tx + 1.0) * 0.5 * panel_w - 0.5, -10.0).astype(np.float32)
        map_v = ((1.0 - y / ty) * 0.5 * panel_h - 0.5).astype(np.float32)
        return cls(row0, row1, np.mod(cols, width), map_u, map_v)


def composite(frame: np.ndarray, rgba: np.ndarray, placement: Placement) -> None:
    """Alpha-composite a premultiplied float RGBA panel onto a uint8 sRGB frame in place through a placement. Tested by test_panels.py::test_placement_centred_and_scaled."""
    warped = cv2.remap(rgba, placement.map_u, placement.map_v, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT,
                       borderValue=(0.0, 0.0, 0.0, 0.0))
    region = frame[placement.row0:placement.row1, placement.cols].astype(np.float32) * (1.0 / 255.0)
    out = warped[..., :3] + region * (1.0 - warped[..., 3:4])
    frame[placement.row0:placement.row1, placement.cols] = np.clip(out * 255.0 + 0.5, 0, 255).astype(np.uint8)


def premultiply(rgba_u8: np.ndarray, size: tuple[int, int] | None = None) -> np.ndarray:
    """Convert straight-alpha uint8 RGBA to premultiplied float32, optionally area-downsampling to (w, h). Tested indirectly by test_hud.py::test_panel_is_premultiplied."""
    arr = np.asarray(rgba_u8, dtype=np.float32) / 255.0
    arr[..., :3] *= arr[..., 3:4]
    if size is not None and (arr.shape[1], arr.shape[0]) != size:
        arr = cv2.resize(arr, size, interpolation=cv2.INTER_AREA)
    return arr
