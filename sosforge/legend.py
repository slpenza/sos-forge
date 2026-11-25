"""A colour-bar legend shared by the regions and grid layers: a title, the colour ramp and round tick values, drawn once
as a flat panel and placed on the sphere at the HUD's anchors, below the presenter panels."""
from __future__ import annotations

import math

import numpy as np
from PIL import Image, ImageDraw

from .colormaps import colormap
from .grid import linear_to_srgb
from .hud import load_font
from .panels import Placement, composite, panel_pixels, premultiply

WIDTH_DEG, HEIGHT_DEG = 34.0, 6.5


def nice_ticks(vmin: float, vmax: float, log: bool) -> list[float]:
    """Return round tick values inside a range: 1-2-5 steps (or powers of ten when that gives over seven) for log scales, at most six for linear. Tested by test_v3_layers.py::test_regions_shade_by_year_with_legend."""
    if log:
        decades = range(math.floor(math.log10(vmin)), math.ceil(math.log10(vmax)) + 1)
        ticks = [m * 10.0 ** e for e in decades for m in (1, 2, 5) if vmin <= m * 10.0 ** e <= vmax]
        if len(ticks) > 7:
            ticks = [t for t in ticks if math.log10(t).is_integer()]
        return ticks if len(ticks) >= 2 else [vmin, vmax]
    # The smallest round step giving at most six ticks. Up to v0.8.8 the steps stopped at 10x, so a range of 60 found
    # none and fell back to a step of 1: 61 ticks.
    base = 10 ** math.floor(math.log10(max(vmax - vmin, 1e-12))) / 10.0
    step = next(base * m for m in (1, 2, 5, 10, 20, 50, 100) if (vmax - vmin) / (base * m) <= 6)
    first = math.ceil(vmin / step) * step
    return [round(first + k * step, 10) for k in range(int(math.floor((vmax - first) / step + 1e-9)) + 1)]


def scale01(v: np.ndarray, lo: float, hi: float, log: bool) -> np.ndarray:
    """Map values to 0..1 between lo and hi on a linear or log scale, clipping outside; on a log scale zero and below map to 0. Tested by test_v3_layers.py::test_regions_shade_by_year_with_legend and test_grid_log_scale_and_legend."""
    v = np.asarray(v)   # in its own precision, as the regions layer always did: forcing float32 shifts log10(100)
    if log:
        v, lo, hi = np.log10(np.maximum(v, 1e-12)), math.log10(max(lo, 1e-12)), math.log10(max(hi, 1e-12))
    return np.clip((v - lo) / max(hi - lo, 1e-12), 0.0, 1.0)


def legend_panel(pw: int, ph: int, cmap: str, title: str, fmt: str, lo: float, hi: float, log: bool) -> np.ndarray:
    """Draw the colour bar with its title and round tick labels as premultiplied RGBA of pw x ph pixels. Tested by test_v3_layers.py::test_regions_shade_by_year_with_legend and test_grid_log_scale_and_legend."""
    s = 3
    w, h = pw * s, ph * s
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, w - 1, h - 1], radius=0.22 * h, fill=(10, 12, 18, 150), outline=(255, 255, 255, 56),
                        width=max(1, int(0.02 * h)))
    m = 0.1 * w
    x0, x1, y0, y1 = m, w - m, 0.42 * h, 0.58 * h
    ramp = linear_to_srgb(colormap(np.linspace(0, 1, int(x1 - x0)), cmap))
    bar = Image.fromarray(np.repeat((ramp[None] * 255).astype(np.uint8), int(y1 - y0), axis=0), "RGB")
    img.paste(bar, (int(x0), int(y0)))
    font_t, font_n = load_font("bold", 0.2 * h), load_font("regular", 0.17 * h)
    d.text((x0, 0.32 * h), title, font=font_t, fill=(242, 244, 248), anchor="ls")
    for tick in nice_ticks(lo, hi, log):
        x = x0 + float(scale01(np.array([tick]), lo, hi, log)[0]) * (x1 - x0)
        d.line([(x, y1), (x, y1 + 0.06 * h)], fill=(255, 255, 255, 160), width=max(1, s))
        d.text((x, y1 + 0.24 * h), fmt.format(tick), font=font_n, fill=(200, 206, 216), anchor="ms")
    return premultiply(np.asarray(img), (pw, ph))


class Legend:
    """A legend panel and its places on the sphere, composited onto finished frames. Tested by test_v3_layers.py::test_regions_shade_by_year_with_legend and test_grid_log_scale_and_legend."""

    def __init__(self, width: int, height: int, anchors, lat: float, **panel):
        """Draw the panel once and place a copy at each anchor longitude, lat degrees north. Tested by the layer tests named on the class."""
        pw, ph = panel_pixels(width, lat, WIDTH_DEG, HEIGHT_DEG)
        self.rgba = legend_panel(pw, ph, **panel)
        self.placements = [Placement.build(width, height, float(lon), lat, WIDTH_DEG, HEIGHT_DEG, pw, ph) for lon in anchors]

    def draw(self, frame: np.ndarray) -> None:
        """Composite every copy of the panel onto a finished uint8 frame. Tested by the layer tests named on the class."""
        for pl in self.placements:
            composite(frame, self.rgba, pl)
