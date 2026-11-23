"""Regions layer: countries (or any polygons) shaded by a value that changes year by year, with borders and a legend.

Regions are rasterised once into an ID image (largest first, so enclaves land on top); each frame looks every region's
value up for the frame's year (interpolated between data years, geometrically on log scales so steady growth looks
steady, and never extrapolated more than a year) and colours the ID image through a small per-region table.
"""
from __future__ import annotations

import math

import cv2
import numpy as np
from PIL import Image, ImageDraw

from ..checks import Check
from ..colormaps import colormap
from ..grid import linear_to_srgb, parse_color
from ..hud import load_font
from ..panels import Placement, composite, panel_pixels, premultiply
from ..recipe import REQUIRED, RecipeError
from ..registry import register_layer
from ..sources import load_source
from ..sources.base import RegionSet
from ..timeline import fractional_year
from .base import Layer

HOLD_YEARS = 1.0


def ring_area_px(ring_px: np.ndarray) -> float:
    """Return a ring's area in square pixels by the shoelace formula. Tested indirectly by test_v3_layers.py::test_rasterise_regions_and_enclaves."""
    x, y = ring_px[:, 0], ring_px[:, 1]
    return float(abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))) / 2.0)


def rasterise(regions: RegionSet, width: int, height: int) -> tuple[np.ndarray, np.ndarray]:
    """Return a uint16 region-ID image (0 = none, i + 1 = region i) and an anti-aliased border mask, largest regions first. Tested by test_v3_layers.py::test_rasterise_regions_and_enclaves."""
    ids = np.zeros((height, width), np.uint16)
    border = np.zeros((height, width), np.uint8)
    to_px = lambda r: np.stack([(r[:, 0] + 180.0) / 360.0 * width, (90.0 - r[:, 1]) / 180.0 * height], axis=1)  # noqa: E731
    shapes = [(sum(ring_area_px(to_px(r)) for r in rings), i, [to_px(r) for r in rings])
              for i, rings in enumerate(regions.rings)]
    thick = max(1, width // 2048)
    for _, i, rings in sorted(shapes, key=lambda s: -s[0]):
        pts = [np.round(r * 16).astype(np.int32) for r in rings]
        cv2.fillPoly(ids, pts, int(i + 1), lineType=cv2.LINE_8, shift=4)
        cv2.polylines(border, pts, True, 255, thick, cv2.LINE_AA, shift=4)
    return ids, border


def nice_ticks(vmin: float, vmax: float, log: bool) -> list[float]:
    """Return round tick values inside a range: 1-2-5 steps (or powers of ten when that gives over seven) for log scales, at most six for linear. Tested by test_v3_layers.py::test_regions_shade_by_year_with_legend."""
    if log:
        decades = range(math.floor(math.log10(vmin)), math.ceil(math.log10(vmax)) + 1)
        ticks = [m * 10.0 ** e for e in decades for m in (1, 2, 5) if vmin <= m * 10.0 ** e <= vmax]
        if len(ticks) > 7:
            ticks = [t for t in ticks if math.log10(t).is_integer()]
        return ticks if len(ticks) >= 2 else [vmin, vmax]
    step = 10 ** math.floor(math.log10(max(vmax - vmin, 1e-12))) / 10.0
    for mult in (1, 2, 5, 10):
        if (vmax - vmin) / (step * mult) <= 5:
            step *= mult
            break
    first = math.ceil(vmin / step) * step
    return [round(first + k * step, 10) for k in range(int((vmax - first) // step) + 1)]


@register_layer("regions")
class RegionsLayer(Layer):
    """Countries or other polygons shaded by a yearly value. Tested by test_v3_layers.py."""

    OPTIONS = {"regions": REQUIRED, "data": REQUIRED, "scale": "linear", "min": None, "max": None, "colormap": "wealth",
               "opacity": 0.85, "no_data_color": "#2a2f3a", "no_data_opacity": 0.55, "border_color": "#ffffff",
               "border_opacity": 0.22, "year": None, "legend": True, "legend_title": "", "legend_format": "{:,.0f}",
               "legend_lat": -46.0, "legend_anchors": None}

    def __init__(self, spec, ctx, index):
        """Validate the scale choice. Tested by test_v3_layers.py::test_regions_shade_by_year_with_legend."""
        super().__init__(spec, ctx, index)
        if self.opts["scale"] not in ("linear", "log"):
            raise RecipeError("regions scale must be 'linear' or 'log'")
        self._loaded = None

    def _load(self):
        """Load the region shapes and the value table once per process. Tested indirectly by test_v3_layers.py::test_regions_shade_by_year_with_legend."""
        if self._loaded is None:
            regions = load_source(self.opts["regions"], self.ctx)
            if not isinstance(regions, RegionSet):
                raise RecipeError(f"regions layer #{self.index + 1}: 'regions' must be a region source such as natural_earth")
            self._loaded = (regions, load_source(self.opts["data"], self.ctx))
        return self._loaded

    def inputs(self) -> dict:
        """Fingerprint both the shapes and the values. Tested indirectly by test_v3_layers.py::test_regions_shade_by_year_with_legend."""
        regions, data = self._load()
        return {"regions": regions.fingerprint, "data": data.fingerprint}

    def prepare(self) -> None:
        """Rasterise the regions, build the region-by-year value matrix (every region sharing a code gets its values) and fix the colour range. Tested by test_v3_layers.py::test_regions_shade_by_year_with_legend."""
        o = self.opts
        regions, data = self._load()
        ids, border = rasterise(regions, self.ctx.width, self.ctx.height)
        index: dict[str, list[int]] = {}
        for i, c in enumerate(regions.codes):
            index.setdefault(c, []).append(i)
        codes, years, vals = data.column("code"), data.column("year").astype(float), data.column("value").astype(float)
        uyears = np.unique(years)
        matrix = np.full((len(regions.codes), len(uyears)), np.nan, np.float32)
        yi = np.searchsorted(uyears, years)
        matched = 0
        for c, j, v in zip(codes, yi, vals):
            rows = index.get(str(c))
            if rows and np.isfinite(v):
                matrix[rows, j] = v
                matched += 1
        finite = matrix[np.isfinite(matrix)]
        if o["scale"] == "log":
            finite = finite[finite > 0]
        vmin = float(o["min"]) if o["min"] is not None else float(np.percentile(finite, 2)) if finite.size else 0.0
        vmax = float(o["max"]) if o["max"] is not None else float(np.percentile(finite, 98)) if finite.size else 1.0
        self.store.put("ids", ids)
        self.store.put("border", border)
        self.store.put("matrix", matrix)
        self.store.put("years", uyears)
        unit = str(data.columns["unit"][0]) if "unit" in getattr(data, "columns", {}) else ""
        self.store.put_json("meta", {"codes": regions.codes, "names": regions.names, "vmin": vmin, "vmax": vmax,
                                     "matched_rows": matched, "rows": int(len(codes)), "unit": unit,
                                     "regions_with_data": int(np.isfinite(matrix).any(axis=1).sum()),
                                     "attribution": [a for a in (regions.attribution, data.attribution) if a],
                                     "license": data.license})

    def attach(self) -> None:
        """Load the ID image, borders, value matrix and colours, and draw the legend panel once. Tested indirectly by test_v3_layers.py::test_regions_shade_by_year_with_legend."""
        o = self.opts
        self.ids, self.border = self.store.get("ids"), self.store.get("border")
        self.matrix, self.years = self.store.get("matrix", mmap=False), self.store.get("years", mmap=False)
        self.meta = self.store.get_json("meta")
        self.no_data, self.border_rgb = parse_color(o["no_data_color"]), parse_color(o["border_color"])
        self.legend = None
        if o["legend"]:
            anchors = o["legend_anchors"] if o["legend_anchors"] is not None else self.ctx.recipe.hud["anchors"]
            pw, ph = panel_pixels(self.ctx.width, o["legend_lat"], 34.0, 6.5)
            self.legend = self._legend(pw, ph)
            self.placements = [Placement.build(self.ctx.width, self.ctx.height, float(lon), o["legend_lat"], 34.0, 6.5, pw, ph)
                               for lon in anchors]

    def norm(self, v: np.ndarray) -> np.ndarray:
        """Map values to 0..1 on the layer's linear or log scale. Tested by test_v3_layers.py::test_regions_shade_by_year_with_legend."""
        lo, hi = self.meta["vmin"], self.meta["vmax"]
        if self.opts["scale"] == "log":
            v, lo, hi = np.log10(np.maximum(v, 1e-12)), math.log10(max(lo, 1e-12)), math.log10(max(hi, 1e-12))
        return np.clip((v - lo) / max(hi - lo, 1e-12), 0.0, 1.0)

    def year_of(self, t) -> float:
        """Return the fractional year a frame shows: the fixed year, the frame's date, or the latest data year. Tested by test_v3_layers.py::test_regions_shade_by_year_with_legend."""
        if self.opts["year"] is not None:
            return float(self.opts["year"])
        if t.date is not None:
            return fractional_year(t.date)
        return float(self.years[-1])

    def values_at(self, year: float) -> np.ndarray:
        """Return each region's value at a year, interpolated between data years (geometrically on log scales) and held at most a year past the ends. Tested by test_v3_layers.py::test_regions_shade_by_year_with_legend."""
        out = np.full(len(self.matrix), np.nan, np.float32)
        log = self.opts["scale"] == "log"
        for i, row in enumerate(self.matrix):
            ok = np.isfinite(row) & ((row > 0) if log else True)
            if not ok.any():
                continue
            ys, vs = self.years[ok], row[ok]
            if ys[0] - HOLD_YEARS <= year <= ys[-1] + HOLD_YEARS:
                out[i] = 10.0 ** np.interp(year, ys, np.log10(vs)) if log else np.interp(year, ys, vs)
        return out

    def render(self, canvas: np.ndarray, t) -> None:
        """Colour every region for the frame's year through a lookup table, then draw the borders, in row strips. Tested by test_v3_layers.py::test_regions_shade_by_year_with_legend."""
        o = self.opts
        v = self.values_at(self.year_of(t))
        have = np.isfinite(v)
        lut = np.zeros((len(v) + 1, 3), np.float32)
        alpha = np.zeros(len(v) + 1, np.float32)
        lut[1:][have] = colormap(self.norm(v[have]), o["colormap"])
        lut[1:][~have] = self.no_data
        alpha[1:] = np.where(have, o["opacity"], o["no_data_opacity"])
        ba = np.float32(o["border_opacity"] / 255.0)
        for y0 in range(0, canvas.shape[0], 256):
            ids = np.asarray(self.ids[y0:y0 + 256])
            a = alpha[ids][..., None]
            canvas[y0:y0 + 256] = canvas[y0:y0 + 256] * (1 - a) + lut[ids] * a
            b = np.asarray(self.border[y0:y0 + 256], np.float32)[..., None] * ba
            canvas[y0:y0 + 256] = canvas[y0:y0 + 256] * (1 - b) + self.border_rgb * b

    def overlay(self, frame: np.ndarray, t) -> None:
        """Composite the legend panels onto the finished frame. Tested by test_v3_layers.py::test_regions_shade_by_year_with_legend."""
        if self.legend is not None:
            for pl in self.placements:
                composite(frame, self.legend, pl)

    def _legend(self, pw: int, ph: int) -> np.ndarray:
        """Draw the colour bar with its title and round tick labels as premultiplied RGBA. Tested by test_v3_layers.py::test_regions_shade_by_year_with_legend."""
        o, s = self.opts, 3
        w, h = pw * s, ph * s
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle([0, 0, w - 1, h - 1], radius=0.22 * h, fill=(10, 12, 18, 150), outline=(255, 255, 255, 56),
                            width=max(1, int(0.02 * h)))
        m = 0.1 * w
        x0, x1, y0, y1 = m, w - m, 0.42 * h, 0.58 * h
        ramp = linear_to_srgb(colormap(np.linspace(0, 1, int(x1 - x0)), o["colormap"]))
        bar = Image.fromarray(np.repeat((ramp[None] * 255).astype(np.uint8), int(y1 - y0), axis=0), "RGB")
        img.paste(bar, (int(x0), int(y0)))
        font_t, font_n = load_font("bold", 0.2 * h), load_font("regular", 0.17 * h)
        d.text((x0, 0.32 * h), o["legend_title"] or self.meta["unit"], font=font_t, fill=(242, 244, 248), anchor="ls")
        lo, hi = self.meta["vmin"], self.meta["vmax"]
        for tick in nice_ticks(lo, hi, o["scale"] == "log"):
            x = x0 + float(self.norm(np.array([tick]))[0]) * (x1 - x0)
            d.line([(x, y1), (x, y1 + 0.06 * h)], fill=(255, 255, 255, 160), width=max(1, s))
            d.text((x, y1 + 0.24 * h), o["legend_format"].format(tick), font=font_n, fill=(200, 206, 216), anchor="ms")
        return premultiply(np.asarray(img), (pw, ph))

    def values(self, t) -> dict:
        """Return the year shown and how many regions have a value then, for HUD templates. Tested by test_v3_layers.py::test_regions_shade_by_year_with_legend."""
        year = self.year_of(t)
        return {"year": int(math.floor(year + 1e-9)), "regions_with_data": int(np.isfinite(self.values_at(year)).sum())}

    def checks(self) -> list[Check]:
        """Verify that data rows matched region codes, reporting how many regions have any data. Tested by test_v3_layers.py::test_regions_shade_by_year_with_legend."""
        m = self.store.get_json("meta")
        return [Check(f"regions #{self.index + 1} matched data", m["regions_with_data"] > 0,
                      f"{m['regions_with_data']} of {len(m['codes'])} regions have data; "
                      f"{m['matched_rows']:,} of {m['rows']:,} rows matched a region code")]

    def credits(self) -> list[str]:
        """Return the region and data attributions. Tested indirectly by test_v3_layers.py::test_regions_shade_by_year_with_legend."""
        m = self.store.get_json("meta") if self.store else {}
        return list(m.get("attribution", []))
