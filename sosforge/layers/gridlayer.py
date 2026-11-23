"""Grid layer: a gridded field (for example NOAA's aurora forecast) drawn as a coloured glow or overlay."""
from __future__ import annotations

import cv2
import numpy as np

from ..checks import Check
from ..colormaps import colormap
from ..density import spherical_gaussian
from ..grid import lon_lat_axes
from ..recipe import REQUIRED, RecipeError
from ..registry import register_layer
from ..sources import load_source
from ..sources.base import GridField
from .base import Layer


def sample_field(field: GridField, width: int, height: int) -> np.ndarray:
    """Resample a regular lat/lon grid onto the output equirectangular grid bilinearly, wrapping when it spans 360 degrees. Tested by test_v3_layers.py::test_sample_field_alignment."""
    v = np.ascontiguousarray(field.values, np.float32)
    rows, cols = v.shape
    lon, lat = lon_lat_axes(width, height)
    lon_d, lat_d = np.degrees(lon), np.degrees(lat)
    wraps = abs(cols * field.dlon - 360.0) < 1e-6
    x = (lon_d - field.lon0) / field.dlon
    x = np.mod(x, cols) if wraps else np.clip(x, 0, cols - 1)
    y = np.clip((lat_d - field.lat0) / field.dlat, 0, rows - 1) + 1.0
    padded = cv2.copyMakeBorder(v, 1, 1, 0, 0, cv2.BORDER_REPLICATE)
    map_x = np.ascontiguousarray(np.broadcast_to(x[None, :], (height, width)), np.float32)
    map_y = np.ascontiguousarray(np.broadcast_to(y[:, None], (height, width)), np.float32)
    border = cv2.BORDER_WRAP if wraps else cv2.BORDER_REPLICATE
    return cv2.remap(padded, map_x, map_y, cv2.INTER_LINEAR, borderMode=border)


@register_layer("grid")
class GridLayer(Layer):
    """Coloured overlay of a gridded field, added as light (the colour carries the strength) or laid over the canvas (the value sets coverage). Tested by test_v3_layers.py::test_grid_layer_glows_and_reports_times."""

    OPTIONS = {"source": REQUIRED, "colormap": "aurora", "min": None, "max": None, "gamma": 1.0, "opacity": 1.0,
               "blend": "add", "smooth_km": 0.0, "body_radius_km": 6371.0088, "attribution": None}

    def __init__(self, spec, ctx, index):
        """Validate the blend mode. Tested by test_v3_layers.py::test_grid_layer_glows_and_reports_times."""
        super().__init__(spec, ctx, index)
        if self.opts["blend"] not in ("add", "over"):
            raise RecipeError("grid blend must be 'add' (glow) or 'over' (paint over)")
        self._field = None

    def _load(self) -> GridField:
        """Load the gridded source once per process. Tested indirectly by test_v3_layers.py::test_grid_layer_glows_and_reports_times."""
        if self._field is None:
            self._field = load_source(self.opts["source"], self.ctx)
            if not isinstance(self._field, GridField):
                raise RecipeError(f"grid layer #{self.index + 1} needs a gridded source such as swpc_aurora")
        return self._field

    def inputs(self) -> dict:
        """Fingerprint the field so a new forecast rebuilds the cache. Tested indirectly by test_v3_layers.py::test_grid_layer_glows_and_reports_times."""
        return {"source": self._load().fingerprint}

    def prepare(self) -> None:
        """Sample, optionally smooth, normalise and colour the field once, storing colour and coverage. Tested by test_v3_layers.py::test_grid_layer_glows_and_reports_times."""
        o, f = self.opts, self._load()
        v = sample_field(f, self.ctx.width, self.ctx.height)
        if o["smooth_km"]:
            v = spherical_gaussian(v, o["smooth_km"] * 1000.0, o["body_radius_km"] * 1000.0, peak_normalize=False)
        vmin = float(o["min"]) if o["min"] is not None else float(np.nanmin(f.values))
        vmax = float(o["max"]) if o["max"] is not None else float(np.nanmax(f.values))
        a = np.clip((v - vmin) / max(vmax - vmin, 1e-9), 0, 1) ** float(o["gamma"])
        k = np.float32(o["opacity"])
        self.store.put("rgb", (colormap(a, o["colormap"]) * (k if o["blend"] == "add" else 1)).astype(np.float16))
        self.store.put("alpha", (a * k).astype(np.float16))
        self.store.put_json("meta", {"times": {k: v.isoformat() for k, v in f.times.items()}, "vmin": vmin, "vmax": vmax,
                                     "peak": float(np.nanmax(f.values)), "attribution": o["attribution"] or f.attribution,
                                     "license": f.license})

    def attach(self) -> None:
        """Memory-map the colour and coverage and parse the field's times. Tested indirectly by test_v3_layers.py::test_grid_layer_glows_and_reports_times."""
        from datetime import datetime
        self.rgb, self.alpha = self.store.get("rgb"), self.store.get("alpha")
        self.meta = self.store.get_json("meta")
        self.times = {k: datetime.fromisoformat(v) for k, v in self.meta["times"].items()}

    def render(self, canvas: np.ndarray, t) -> None:
        """Add the coloured field as light, or lay it over the canvas by its coverage, in row strips. Tested by test_v3_layers.py::test_grid_layer_glows_and_reports_times."""
        for y0 in range(0, canvas.shape[0], 128):
            c = self.rgb[y0:y0 + 128].astype(np.float32)
            if self.opts["blend"] == "add":
                canvas[y0:y0 + 128] += c
            else:
                a = self.alpha[y0:y0 + 128].astype(np.float32)[..., None]
                canvas[y0:y0 + 128] = canvas[y0:y0 + 128] * (1 - a) + c * a

    def values(self, t) -> dict:
        """Return the field's named times (such as forecast_time) and its peak value for HUD templates. Tested by test_v3_layers.py::test_grid_layer_glows_and_reports_times."""
        return {**self.times, "grid_peak": self.meta["peak"]}

    def checks(self) -> list[Check]:
        """Verify the field has values above the display minimum. Tested by test_v3_layers.py::test_grid_layer_glows_and_reports_times."""
        m = self.store.get_json("meta")
        return [Check(f"grid #{self.index + 1} has data", m["peak"] > m["vmin"],
                      f"peak {m['peak']:.3g}, shown from {m['vmin']:.3g} to {m['vmax']:.3g}")]

    def credits(self) -> list[str]:
        """Return the field's attribution. Tested indirectly by test_v3_layers.py::test_grid_layer_glows_and_reports_times."""
        m = self.store.get_json("meta") if self.store else {}
        return [m["attribution"]] if m.get("attribution") else []

