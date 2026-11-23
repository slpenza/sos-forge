"""Point/track density on the sphere: bilinear deposit plus a Gaussian whose ground size is the same at every
latitude (horizontal sigma grows as 1/cos(lat) in equirectangular pixels), peak-normalised so one isolated
unit-weight point reads 1.0 at its centre anywhere on the globe."""
from __future__ import annotations

import math

import cv2
import numpy as np

from .grid import latlon_to_px

MIN_SIGMA_PX = 0.6


def deposit(lat_deg: np.ndarray, lon_deg: np.ndarray, weights: np.ndarray | None, width: int,
            height: int) -> np.ndarray:
    """Splat weighted points onto an (H, W) float32 grid with bilinear weights, wrapping in longitude. Tested by test_density.py::test_deposit_conserves_weight_and_wraps."""
    out = np.zeros(height * width, dtype=np.float64)
    if len(lat_deg) == 0:
        return out.reshape(height, width).astype(np.float32)
    w = np.ones(len(lat_deg)) if weights is None else np.asarray(weights, dtype=np.float64)
    x, y = latlon_to_px(lat_deg, lon_deg, width, height)
    x0, y0 = np.floor(x).astype(np.int64), np.floor(y).astype(np.int64)
    fx, fy = x - x0, y - y0
    for dx, dy, wt in ((0, 0, (1 - fx) * (1 - fy)), (1, 0, fx * (1 - fy)), (0, 1, (1 - fx) * fy), (1, 1, fx * fy)):
        xi = (x0 + dx) % width
        yi = np.clip(y0 + dy, 0, height - 1)
        out += np.bincount(yi * width + xi, weights=w * wt, minlength=height * width)
    return out.reshape(height, width).astype(np.float32)


def _gauss(sigma: float) -> tuple[np.ndarray, int]:
    """Return a normalised 1-D Gaussian kernel and its radius for a pixel sigma. Tested indirectly by test_density.py::test_glow_same_angular_size_at_any_latitude."""
    r = max(1, int(math.ceil(4.0 * sigma)))
    return cv2.getGaussianKernel(2 * r + 1, sigma, cv2.CV_32F), r


def spherical_gaussian(img: np.ndarray, sigma_m: float, body_radius_m: float, peak_normalize: bool = True) -> np.ndarray:
    """Blur an equirectangular grid with a ground-distance Gaussian that stays round on the sphere at every latitude. Tested by test_density.py::test_glow_same_angular_size_at_any_latitude and test_blur_is_seamless."""
    h_px, w_px = img.shape
    sy = max(MIN_SIGMA_PX, sigma_m / (body_radius_m * math.pi / h_px))
    ky, _ = _gauss(sy)
    one = np.ones((1, 1), np.float32)
    out = cv2.sepFilter2D(img.astype(np.float32), -1, one, ky, borderType=cv2.BORDER_REFLECT)
    lat = np.pi / 2.0 - ((np.arange(h_px) + 0.5) / h_px) * np.pi
    sx = np.minimum(sy / np.maximum(np.cos(lat), 1e-4), w_px / 3.0)
    key = np.round(np.log(sx) / math.log(1.08)).astype(int)
    starts = np.flatnonzero(np.r_[True, key[1:] != key[:-1]])
    ends = np.r_[starts[1:], h_px]
    for a, b in zip(starts, ends):
        s = float(np.median(sx[a:b]))
        band = out[a:b]
        if 4.0 * s >= w_px:
            out[a:b] = band.mean(axis=1, keepdims=True)
            if peak_normalize:
                out[a:b] *= np.float32(w_px * math.sqrt(2.0 * math.pi) * sy)
            continue
        kx, r = _gauss(s)
        padded = np.concatenate([band[:, w_px - r:], band, band[:, :r]], axis=1)
        out[a:b] = cv2.sepFilter2D(padded, -1, kx, one, borderType=cv2.BORDER_CONSTANT)[:, r:r + w_px]
        if peak_normalize:
            out[a:b] *= np.float32(2.0 * math.pi * s * sy)
    return out


def glow(density: np.ndarray, gain: float) -> np.ndarray:
    """Map accumulated density to a soft-saturating 0..1 glow, 1 - exp(-gain * density). Tested by test_density.py::test_glow_same_angular_size_at_any_latitude."""
    return -np.expm1(-np.float32(gain) * np.maximum(density, 0.0))
