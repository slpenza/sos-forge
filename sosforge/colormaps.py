"""Named colour ramps (sRGB stops, interpolated in linear light) shared by the tracks, grid and regions layers."""
from __future__ import annotations

import numpy as np

from .grid import parse_color
from .recipe import RecipeError

COLORMAPS = {
    "fire": [(0.0, "#000000"), (0.25, "#7a0000"), (0.5, "#ff3b00"), (0.75, "#ffb000"), (1.0, "#fff6d8")],
    "ice": [(0.0, "#000000"), (0.3, "#0b2a6f"), (0.6, "#1e88e5"), (0.85, "#7fdbff"), (1.0, "#ffffff")],
    "aurora": [(0.0, "#000000"), (0.25, "#0a5c32"), (0.55, "#1fd67a"), (0.8, "#a8ff6e"), (1.0, "#ff7ad9")],
    "wealth": [(0.0, "#2b1a4a"), (0.3, "#6a2c86"), (0.55, "#c2477a"), (0.8, "#f28f3b"), (1.0, "#ffe08a")],
    "sea": [(0.0, "#0b1e3a"), (0.35, "#145a8c"), (0.65, "#2bb0b0"), (1.0, "#d8f5c8")],
}


def colormap(values: np.ndarray, name: str) -> np.ndarray:
    """Map 0..1 values to linear-light RGB with a named ramp, or a single '#rrggbb' colour scaled by value. Tested by test_layers.py::test_colormap_and_trim and test_v3_layers.py::test_regions_shade_by_year_with_legend."""
    v = np.clip(values, 0.0, 1.0).astype(np.float32)
    if name.startswith("#"):
        return v[..., None] * parse_color(name)
    if name not in COLORMAPS:
        raise RecipeError(f"unknown colormap '{name}' (use {', '.join(COLORMAPS)} or '#rrggbb')")
    stops = COLORMAPS[name]
    xs = np.array([s for s, _ in stops], np.float32)
    cs = np.stack([parse_color(c) for _, c in stops])
    return np.stack([np.interp(v, xs, cs[:, ch]) for ch in range(3)], axis=-1).astype(np.float32)
