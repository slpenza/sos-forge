"""Tests for gnomonic panel placement."""
from __future__ import annotations

import math

import numpy as np

from sosforge.panels import Placement, composite, forward_gnomonic, inverse_gnomonic, panel_pixels


def test_gnomonic_round_trip():
    """Checks inverse then forward gnomonic projection recovers tangent-plane points around 30 S. Covers inverse_gnomonic and forward_gnomonic; this test is not itself tested."""
    rng = np.random.default_rng(1)
    x, y = rng.uniform(-0.4, 0.4, 200), rng.uniform(-0.1, 0.1, 200)
    lat, dlon = inverse_gnomonic(x, y, math.radians(-30))
    x2, y2, c = forward_gnomonic(lat, dlon, math.radians(-30))
    assert np.allclose(x, x2) and np.allclose(y, y2) and np.all(c > 0)


def _stamp(w: int, lon: float, lat: float) -> np.ndarray:
    """Composite an opaque white 20x10 deg panel onto a black frame and return the frame. Helper; not itself tested."""
    pw, ph = panel_pixels(w, lat, 20, 10)
    pl = Placement.build(w, w // 2, lon, lat, 20, 10, pw, ph)
    frame = np.zeros((w // 2, w, 3), np.uint8)
    composite(frame, np.ones((ph, pw, 4), np.float32), pl)
    return frame


def test_placement_centred_and_scaled():
    """Checks a panel lands centred on its anchor and its raster size scales with output width. Covers Placement.build, composite and panel_pixels; this test is not itself tested."""
    w = 1024
    ys, xs = np.nonzero(_stamp(w, 0, -30)[..., 0] > 128)
    assert abs(xs.mean() - w / 2) < 3 and abs(ys.mean() - (90 + 30) / 180 * (w // 2)) < 3
    a, b = panel_pixels(1024, -30, 40, 10), panel_pixels(4096, -30, 40, 10)
    assert abs(b[0] / a[0] - 4) < 0.05


def test_placement_wraps_seam():
    """Checks a panel anchored at 180 deg is split evenly across the left and right edges. Covers Placement.build column wrapping; this test is not itself tested."""
    w = 1024
    f = _stamp(w, 180, 20)[..., 0] > 128
    left, right = f[:, : w // 4].sum(), f[:, 3 * w // 4:].sum()
    assert left > 0 and abs(left - right) / max(left, right) < 0.1 and f[:, w // 4: 3 * w // 4].sum() == 0
