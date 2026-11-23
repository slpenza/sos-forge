"""Tracks layer: GPS activities as a heatmap glow on the globe plus an optional zoomed inset panel.

On a 6-foot globe a city spans a few pixels, so personal activity reads as a glowing dot; the inset shows the
same tracks in a local tangent-plane view.  The first and last privacy_trim_m of each track are removed because
raw exports include home start/end points even when an app's privacy settings hide them from others.
"""
from __future__ import annotations

import math

import cv2
import numpy as np
from PIL import Image, ImageDraw

from ..checks import Check
from ..colormaps import colormap  # noqa: F401  (re-exported for callers of the tracks layer)
from ..density import glow, spherical_gaussian
from ..grid import great_circle_m, latlon_to_px, linear_to_srgb, unit_vectors, vectors_to_latlon
from ..hud import load_font, render_template
from ..panels import Placement, composite, forward_gnomonic, panel_pixels, premultiply
from ..recipe import REQUIRED, RecipeError
from ..registry import register_layer
from ..sources import load_source
from .base import Layer

def trim_ends(lat: np.ndarray, lon: np.ndarray, trim_m: float) -> tuple[np.ndarray, np.ndarray]:
    """Drop points within trim_m of a track's start and end along the path, returning empty arrays for short tracks. Tested by test_layers.py::test_colormap_and_trim."""
    if trim_m <= 0 or len(lat) < 2:
        return lat, lon
    seg = great_circle_m(lat[:-1], lon[:-1], lat[1:], lon[1:])
    s = np.r_[0.0, np.cumsum(seg)]
    keep = (s >= trim_m) & (s <= s[-1] - trim_m)
    return lat[keep], lon[keep]


def densify(lat: np.ndarray, lon: np.ndarray, step_m: float) -> tuple[np.ndarray, np.ndarray]:
    """Insert great-circle points so consecutive samples are at most step_m apart. Tested by test_layers.py::test_colormap_and_trim."""
    if len(lat) < 2:
        return lat, lon
    v = unit_vectors(lat, lon)
    seg = great_circle_m(lat[:-1], lon[:-1], lat[1:], lon[1:])
    n = np.maximum(1, np.ceil(seg / step_m).astype(int))
    reps = np.repeat(np.arange(len(seg)), n)
    f = (np.concatenate([np.arange(k) for k in n]) / np.repeat(n, n))[:, None]
    pts = v[reps] * (1 - f) + v[reps + 1] * f
    la, lo = vectors_to_latlon(np.vstack([pts, v[-1:]]))
    return la, lo


@register_layer("tracks")
class TracksLayer(Layer):
    """Heat-style glow of GPS tracks with a local-detail inset panel. Tested by test_layers.py::test_tracks_glow_inset_and_values."""

    OPTIONS = {"source": REQUIRED, "privacy_trim_m": 400.0, "width_km": 6.0, "gain": 1.2, "colormap": "fire",
               "intensity": 1.0, "inset": True, "inset_lon": None, "inset_lat": 20.0, "inset_size_deg": 36.0,
               "inset_label": "{count} activities  ·  {span_km:,.0f} km across", "body_radius_km": 6371.0088,
               "attribution": None}

    def __init__(self, spec, ctx, index):
        """Validate options and defer loading the source. Tested by test_layers.py::test_layer_option_validation."""
        super().__init__(spec, ctx, index)
        self._set = None

    def _load(self):
        """Load the track files once per process. Tested indirectly by test_layers.py::test_tracks_glow_inset_and_values."""
        if self._set is None:
            self._set = load_source(self.opts["source"], self.ctx)
        return self._set

    def inputs(self) -> dict:
        """Fingerprint the activity files. Tested indirectly by test_build.py::test_cache_reused_and_invalidated."""
        return {"source": self._load().fingerprint}

    def prepare(self) -> None:
        """Trim, densify and rasterise each track once per pixel, then store the coloured glow, stats and inset panel. Tested by test_layers.py::test_tracks_glow_inset_and_values."""
        o, W, H = self.opts, self.ctx.width, self.ctx.height
        body = o["body_radius_km"] * 1000.0
        step = 0.4 * (2 * math.pi * body / W)
        counts = np.zeros(H * W, np.float32)
        kept, total_m, times = [], 0.0, []
        for tr in self._load().tracks:
            la, lo = trim_ends(np.asarray(tr.lat, float), np.asarray(tr.lon, float), o["privacy_trim_m"])
            if len(la) < 2:
                continue
            total_m += float(great_circle_m(la[:-1], lo[:-1], la[1:], lo[1:]).sum())
            if tr.time is not None and np.isfinite(tr.time).any():
                times.append(float(np.nanmin(tr.time)))
            kept.append((la, lo))
            dl, dn = densify(la, lo, step)
            x, y = latlon_to_px(dl, dn, W, H)
            idx = np.unique(np.clip(np.round(y).astype(int), 0, H - 1) * W + np.round(x).astype(int) % W)
            counts[idx] += 1.0
        density = spherical_gaussian(counts.reshape(H, W), o["width_km"] * 1000.0, body)
        g = glow(density, o["gain"])
        self.store.put("rgb", (colormap(g, o["colormap"]) * np.float32(o["intensity"])).astype(np.float16))
        stats = {"count": len(kept), "distance_km": total_m / 1000.0, "span_km": 0.0, "center": [0.0, 0.0],
                 "first": min(times) if times else None, "last": max(times) if times else None,
                 "attribution": o["attribution"] or self._load().attribution}
        if kept:
            allv = unit_vectors(np.concatenate([k[0] for k in kept]), np.concatenate([k[1] for k in kept]))
            c = allv.mean(axis=0)
            clat, clon = vectors_to_latlon(c[None, :])
            ang = np.arccos(np.clip(allv @ (c / np.linalg.norm(c)), -1, 1))
            ext = max(float(np.percentile(ang, 98)) * 1.15, 2000.0 / body)
            stats.update(center=[float(clat[0]), float(clon[0])], span_km=2 * ext * body / 1000.0, extent_rad=ext)
            if o["inset"]:
                self.store.put("inset", self._inset(kept, stats).astype(np.float16))
        self.store.put_json("stats", stats)

    def _inset_anchor(self, stats: dict) -> tuple[float, float]:
        """Return the inset's (lon, lat) on the globe, defaulting to the side opposite the tracks. Tested by test_layers.py::test_tracks_glow_inset_and_values."""
        lon = self.opts["inset_lon"]
        if lon is None:
            lon = (stats["center"][1] + 180.0 + 180.0) % 360.0 - 180.0
        return float(lon), float(self.opts["inset_lat"])

    def _inset(self, kept: list, stats: dict) -> np.ndarray:
        """Draw the local tangent-plane view of all tracks with a label as premultiplied RGBA. Tested by test_layers.py::test_tracks_glow_inset_and_values."""
        o = self.opts
        _, lat_deg = self._inset_anchor(stats)
        pw, ph = panel_pixels(self.ctx.width, lat_deg, o["inset_size_deg"], o["inset_size_deg"])
        n = min(pw, ph)
        ss = 2
        size = n * ss
        T = math.tan(stats["extent_rad"])
        clat, clon = math.radians(stats["center"][0]), stats["center"][1]
        accum = np.zeros((size, size), np.float32)
        thick = max(1, int(round(size / 400)))
        for la, lo in kept:
            x, y, _ = forward_gnomonic(np.radians(la), np.radians(lo - clon), clat)
            pts = np.stack([(x / T + 1) * 0.5 * size, (1 - y / T) * 0.5 * size], axis=1)
            mask = np.zeros((size, size), np.uint8)
            cv2.polylines(mask, [np.round(pts * 16).astype(np.int32)], False, 255, thick, cv2.LINE_AA, shift=4)
            accum += mask.astype(np.float32) / 255.0
        heat = colormap(1.0 - np.exp(-o["gain"] * accum), o["colormap"])
        rgb = linear_to_srgb(heat)
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.rounded_rectangle([0, 0, size - 1, size - 1], radius=0.06 * size, fill=(8, 10, 14, 210),
                               outline=(255, 255, 255, 70), width=max(1, size // 300))
        lines = Image.fromarray(np.dstack([rgb * 255, np.clip(accum, 0, 1) * 255]).astype(np.uint8), "RGBA")
        img.alpha_composite(lines)
        font = load_font("regular", size * 0.045)
        label = render_template(o["inset_label"], {**stats, "count": stats["count"]})
        draw.text((size * 0.05, size * 0.95), label, font=font, fill=(200, 206, 216, 255), anchor="ls")
        return premultiply(np.asarray(img), (n, n))

    def attach(self) -> None:
        """Load the glow, stats and inset, and precompute the inset placement. Tested indirectly by test_build.py::test_texture_build_end_to_end."""
        self.rgb = self.store.get("rgb")
        self.stats = self.store.get_json("stats")
        self.inset = self.store.get("inset", mmap=False).astype(np.float32) if self.store.has("inset") else None
        if self.inset is not None:
            lon, lat = self._inset_anchor(self.stats)
            n = self.inset.shape[0]
            self.placement = Placement.build(self.ctx.width, self.ctx.height, lon, lat, self.opts["inset_size_deg"],
                                             self.opts["inset_size_deg"], n, n)

    def render(self, canvas: np.ndarray, t) -> None:
        """Add the track glow to the canvas. Tested by test_layers.py::test_tracks_glow_inset_and_values."""
        canvas += self.rgb

    def overlay(self, frame: np.ndarray, t) -> None:
        """Composite the inset panel onto the sRGB frame. Tested by test_layers.py::test_tracks_glow_inset_and_values."""
        if self.inset is not None:
            composite(frame, self.inset, self.placement)

    def values(self, t) -> dict:
        """Return count, distance_km and span_km for HUD templates. Tested by test_layers.py::test_tracks_glow_inset_and_values."""
        s = self.stats
        return {"count": s["count"], "distance_km": s["distance_km"], "span_km": s["span_km"]}

    def checks(self) -> list[Check]:
        """Verify at least one track survived privacy trimming. Tested by test_layers.py::test_tracks_glow_inset_and_values."""
        s = self.store.get_json("stats")
        return [Check(f"tracks #{self.index + 1} has activities", s["count"] > 0,
                      f"{s['count']} activities, {s['distance_km']:,.0f} km after trimming {self.opts['privacy_trim_m']:.0f} m from each end")]

    def credits(self) -> list[str]:
        """Return the source attribution if any. Tested indirectly by test_build.py::test_texture_build_end_to_end."""
        s = self.store.get_json("stats") if self.store else {}
        return [s["attribution"]] if s.get("attribution") else []

