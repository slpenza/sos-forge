"""Points layer: glowing dots for any lat/lon table, with optional weights, categories, a category spotlight cycle,
and time-based reveal (points appear on their date and flash briefly when new)."""
from __future__ import annotations

import math

import numpy as np

from ..checks import Check
from ..density import deposit, glow, spherical_gaussian
from ..grid import parse_color
from ..recipe import REQUIRED, RecipeError
from ..registry import register_layer
from ..sources import load_source
from ..sources.base import to_epoch_days
from ..timeline import fractional_year
from .base import Layer

DEFAULT_PALETTE = ["#5ee07a", "#ffd166", "#ff6b6b", "#7cc8ff", "#ff9f43", "#c792ea", "#f78fb3", "#b8e986",
                   "#4dd0e1", "#ffab91"]


def smoothstep(x):
    """Return the cubic smoothstep of x clamped to [0, 1]. Tested indirectly by test_layers.py::test_points_cycle_spotlight."""
    x = np.clip(x, 0.0, 1.0)
    return x * x * (3.0 - 2.0 * x)


@register_layer("points")
class PointsLayer(Layer):
    """Additive glow for point data in linear light. Tested by test_layers.py."""

    OPTIONS = {"source": REQUIRED, "weight": None, "weight_scale": "linear", "time": None, "category": None,
               "categories": None, "palette": {}, "color": "#ffb000", "radius_km": 15.0, "halo_km": 80.0,
               "core_gain": 0.9, "halo_gain": 0.08, "halo_strength": 0.6, "intensity": 1.0, "cycle": False,
               "cycle_all_label": "All", "cycle_dim": 0.12, "cycle_fade": 0.12, "flash_days": 0.0,
               "flash_color": "#ffffff", "body_radius_km": 6371.0088, "attribution": None, "weight_series": None,
               "weight_ref": None}

    def __init__(self, spec, ctx, index):
        """Validate option combinations such as cycle needing a category column. Tested by test_layers.py::test_layer_option_validation."""
        super().__init__(spec, ctx, index)
        if self.opts["cycle"] and not self.opts["category"]:
            raise RecipeError(f"points layer #{index + 1}: cycle = true needs category = \"<column>\"")
        if self.opts["weight_scale"] not in ("linear", "sqrt", "log"):
            raise RecipeError("points weight_scale must be linear, sqrt or log")
        ws = self.opts["weight_series"]
        if ws and ("{year}" not in ws or self.opts["weight"] or self.opts["time"]):
            raise RecipeError("points weight_series is a column template containing {year} (e.g. \"pop_{year}\") "
                              "and replaces weight and time")
        ref = self.opts["weight_ref"]
        if ref is not None and ref != "auto" and not (isinstance(ref, (int, float)) and ref > 0):
            raise RecipeError('points weight_ref must be "auto" or a positive number')
        self.ref = 1.0
        self._table = None

    def _load(self):
        """Load the source once per process and memoise it. Tested indirectly by test_layers.py::test_points_static_glow_and_values."""
        if self._table is None:
            self._table = load_source(self.opts["source"], self.ctx)
        return self._table

    def inputs(self) -> dict:
        """Fingerprint the source data so refreshed downloads rebuild the cache. Tested indirectly by test_build.py::test_cache_reused_and_invalidated."""
        return {"source": self._load().fingerprint}

    def _scale(self, w: np.ndarray) -> np.ndarray:
        """Apply the weight_scale (linear, sqrt or log) to raw weights and divide by the reference weight for drawing. Tested by test_layers.py::test_points_weight_series."""
        w = np.maximum(np.asarray(w, float), 0.0)
        return {"linear": w, "sqrt": np.sqrt(w), "log": np.log1p(w)}[self.opts["weight_scale"]] / self.ref

    def _reference(self, w: np.ndarray) -> float:
        """Return the weight that draws at full strength: 1, a fixed number, or for "auto" the 99th percentile of scaled weights. Tested by test_layers.py::test_points_weight_series."""
        ref = self.opts["weight_ref"]
        if ref is None:
            return 1.0
        if ref != "auto":
            return float(ref)
        self.ref = 1.0
        s = self._scale(w)
        s = s[s > 0]
        return float(np.percentile(s, 99)) if s.size else 1.0

    def series_columns(self, table) -> tuple[list[str], np.ndarray]:
        """Return the columns matching weight_series and their years in ascending order. Tested by test_layers.py::test_points_weight_series."""
        import re
        pattern = re.compile("^" + re.escape(self.opts["weight_series"]).replace(r"\{year\}", r"(\d{4})") + "$")
        found = sorted((int(m.group(1)), c) for c in table.columns if (m := pattern.match(c)))
        if len(found) < 2:
            raise RecipeError(f"weight_series {self.opts['weight_series']!r} matches {len(found)} columns; needs 2 or more")
        return [c for _, c in found], np.array([y for y, _ in found], float)

    def prepare(self) -> None:
        """Clean coordinates, keep raw weights or a per-year weight series, map categories to colours, parse times, and precompute glow when static. Tested by test_layers.py::test_points_static_glow_and_values, test_points_time_reveal and test_points_weight_series."""
        o, table = self.opts, self._load()
        lat, lon = np.asarray(table.column("lat"), float), np.asarray(table.column("lon"), float)
        ok = np.isfinite(lat) & np.isfinite(lon) & (np.abs(lat) <= 90) & (np.abs(lon) <= 360)
        series = None
        if o["weight_series"]:
            cols, years = self.series_columns(table)
            series = np.nan_to_num(np.stack([np.asarray(table.column(c), float) for c in cols], axis=1))
            w = series.max(axis=1)
        else:
            w = np.ones(len(lat)) if not o["weight"] else np.nan_to_num(np.asarray(table.column(o["weight"]), float))
        when = to_epoch_days(table.column(o["time"])) if o["time"] else np.zeros(len(lat))
        if o["time"]:
            ok &= np.isfinite(when)
        if o["category"]:
            raw = np.asarray(table.column(o["category"])).astype(str)
            names = list(o["categories"] or dict.fromkeys(raw.tolist()))
            index = {n: i for i, n in enumerate(names)}
            cat = np.array([index.get(v, -1) for v in raw])
            ok &= cat >= 0
        else:
            names, cat = [o["cycle_all_label"]], np.zeros(len(lat), int)
        colors = [o["palette"].get(n) or (o["color"] if not o["category"] else DEFAULT_PALETTE[i % len(DEFAULT_PALETTE)])
                  for i, n in enumerate(names)]
        lat, lon, w, when, cat = lat[ok], lon[ok], w[ok], when[ok], cat[ok]
        self.ref = self._reference(w)
        self.store.put("pts", np.stack([lat, lon, w, when, cat.astype(float)]))
        if series is not None:
            self.store.put("series", series[ok].astype(np.float32))
            self.store.put("series_years", years)
        meta = {"names": names, "colors": colors, "counts": [int((cat == i).sum()) for i in range(len(names))],
                "weights": [float(w[cat == i].sum()) for i in range(len(names))], "dropped": int((~ok).sum()),
                "attribution": o["attribution"] or table.attribution, "license": table.license,
                "weight_ref": self.ref, "times": {k: v.isoformat() for k, v in table.times.items()}, "stats": table.stats}
        self.store.put_json("meta", meta)
        if not o["time"] and series is None:
            self.store.put("glow", np.stack([self._glow(lat[cat == i], lon[cat == i], w[cat == i])
                                             for i in range(len(names))]).astype(np.float16))

    def _glow(self, lat, lon, w) -> np.ndarray:
        """Return core plus halo glow (0..~1.6) for a set of points at output resolution. Tested by test_layers.py::test_points_static_glow_and_values."""
        o, W, H = self.opts, self.ctx.width, self.ctx.height
        r = o["body_radius_km"] * 1000.0
        d = deposit(lat, lon, self._scale(w), W, H)
        core = glow(spherical_gaussian(d, o["radius_km"] * 1000.0, r), o["core_gain"])
        if o["halo_strength"] <= 0:
            return core
        halo = glow(spherical_gaussian(d, o["halo_km"] * 1000.0, r), o["halo_gain"])
        return core + np.float32(o["halo_strength"]) * halo

    def attach(self) -> None:
        """Load points, metadata, colours and any precomputed glow. Tested indirectly by test_build.py::test_movie_build_end_to_end."""
        self.pts = self.store.get("pts", mmap=False)
        self.meta = self.store.get_json("meta")
        self.ref = float(self.meta.get("weight_ref", 1.0))
        self.colors = np.stack([parse_color(c) for c in self.meta["colors"]])
        self.static = self.store.get("glow") if self.store.has("glow") else None
        self.series = self.store.get("series", mmap=False) if self.store.has("series") else None
        self.series_years = self.store.get("series_years", mmap=False) if self.store.has("series_years") else None

    def _segment(self, t) -> tuple[np.ndarray, int]:
        """Return per-category brightness and the label index for the spotlight cycle at a frame. Tested by test_layers.py::test_points_cycle_spotlight."""
        n = len(self.meta["names"])
        if not self.opts["cycle"]:
            return np.ones(n, np.float32), -1
        segs = n + 1
        pos = (t.fraction % 1.0) * segs
        k = int(math.floor(pos)) % segs
        frac = pos - math.floor(pos)
        dim = float(self.opts["cycle_dim"])

        def level(seg: int) -> np.ndarray:
            """Return the brightness vector for one spotlight segment, where the last segment shows all. Tested indirectly by test_layers.py::test_points_cycle_spotlight."""
            return np.ones(n, np.float32) if seg == n else np.where(np.arange(n) == seg, 1.0, dim).astype(np.float32)

        fade = max(float(self.opts["cycle_fade"]), 1e-6)
        mix = float(smoothstep((frac - (1.0 - fade)) / fade))
        label = (k + 1) % segs if mix >= 0.5 else k
        return level(k) * (1 - mix) + level((k + 1) % segs) * mix, label

    def render(self, canvas: np.ndarray, t) -> None:
        """Add the glow of visible points to the canvas, using precomputed glow when static or per-frame density when timed. Tested by test_layers.py::test_points_static_glow_and_values and test_points_time_reveal."""
        alpha, _ = self._segment(t)
        k = np.float32(self.opts["intensity"])
        if self.static is not None:
            coef = (alpha[:, None] * self.colors * k).astype(np.float32)
            for y0 in range(0, canvas.shape[0], 128):
                g = np.asarray(self.static[:, y0:y0 + 128], dtype=np.float32)
                canvas[y0:y0 + 128] += np.tensordot(g, coef, axes=([0], [0]))
            return
        lat, lon, w, when, cat = self.pts
        now = self._now_days(t)
        w, vis = self._weights(t, w, when, now)
        for i, a in enumerate(alpha):
            m = vis & (cat == i)
            if a > 0 and m.any():
                canvas += self._glow(lat[m], lon[m], w[m])[..., None] * (self.colors[i] * k * a)
        if self.opts["flash_days"] > 0 and self.series is None:
            age = now - when
            m = vis & (age < self.opts["flash_days"])
            if m.any():
                fw = self._scale(w[m]) * (1.0 - age[m] / self.opts["flash_days"])
                d = deposit(lat[m], lon[m], fw, self.ctx.width, self.ctx.height)
                g = glow(spherical_gaussian(d, self.opts["radius_km"] * 1000.0, self.opts["body_radius_km"] * 1000.0),
                         self.opts["core_gain"])
                canvas += g[..., None] * (parse_color(self.opts["flash_color"]) * k)

    def _weights(self, t, w, when, now) -> tuple[np.ndarray, np.ndarray]:
        """Return each point's raw weight and visibility at a frame, interpolating a weight series by the frame's year. Tested by test_layers.py::test_points_weight_series."""
        if self.series is None:
            return w, when <= now
        year = fractional_year(t.date) if t.date else float(self.series_years[-1])
        ys = self.series_years
        j = int(np.clip(np.searchsorted(ys, year) - 1, 0, len(ys) - 2))
        f = float(np.clip((year - ys[j]) / (ys[j + 1] - ys[j]), 0.0, 1.0))
        wt = self.series[:, j] * (1 - f) + self.series[:, j + 1] * f
        return wt, wt > 0

    def _now_days(self, t) -> float:
        """Return the frame's date in epoch days, or +inf when the timeline has no dates. Tested by test_layers.py::test_points_time_reveal."""
        return t.date.timestamp() / 86400.0 if t.date else float("inf")

    def values(self, t) -> dict:
        """Return count, weight_sum, category and categories for HUD templates at this frame, plus the source's named times and numbers (such as fires_to, or strongest). Tested by test_layers.py::test_points_cycle_spotlight, test_points_time_reveal and test_v3_layers.py::test_points_pass_source_times."""
        _, label = self._segment(t)
        lat, lon, w, when, cat = self.pts
        w, vis = self._weights(t, w, when, self._now_days(t))
        if label >= 0 and label < len(self.meta["names"]):
            vis = vis & (cat == label)
            name = self.meta["names"][label]
        else:
            name = self.opts["cycle_all_label"]
        from datetime import datetime
        times = {k: datetime.fromisoformat(v) for k, v in self.meta.get("times", {}).items()}
        return {"count": int(vis.sum()), "weight_sum": float(w[vis].sum()), "category": name,
                "categories": len(self.meta["names"]), **times, **self.meta.get("stats", {})}

    def checks(self) -> list[Check]:
        """Verify the source produced usable points and report per-category counts and dropped rows. Tested by test_layers.py::test_points_static_glow_and_values."""
        meta = self.store.get_json("meta")
        total = sum(meta["counts"])
        detail = ", ".join(f"{n} {c:,}" for n, c in zip(meta["names"], meta["counts"]))
        return [Check(f"points #{self.index + 1} has data", total > 0,
                      f"{total:,} points ({detail}); {meta['dropped']} rows dropped for bad coordinates/dates")]

    def credits(self) -> list[str]:
        """Return the source attribution. Tested by test_layers.py::test_points_static_glow_and_values."""
        meta = self.store.get_json("meta") if self.store else {}
        return [meta["attribution"]] if meta.get("attribution") else []
