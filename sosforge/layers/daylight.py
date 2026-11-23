"""Daylight: a day image where the Sun is up and a night image where it is down, blended through twilight, for the real
Sun position at the frame's date (or a fixed moment, or the build time)."""
from __future__ import annotations

import math
from datetime import datetime, timezone

import numpy as np

from ..basemaps import BASEMAPS, basemap_credit, fetch_basemap, load_rgb
from ..grid import lon_lat_axes, resample_equirect, srgb_to_linear
from ..recipe import RecipeError
from ..registry import register_layer
from ..sun import subsolar_point
from ..timeline import parse_date
from .base import Layer


@register_layer("daylight")
class DaylightLayer(Layer):
    """Day and night imagery split by the Sun's real position. Tested by test_v3_layers.py::test_daylight_follows_the_sun."""

    OPTIONS = {"day": "blue_marble", "night": "black_marble", "day_brightness": 1.0, "night_brightness": 1.0,
               "twilight_deg": 12.0, "at": "timeline"}

    def __init__(self, spec, ctx, index):
        """Validate the time choice: timeline, now, or an ISO date-time. Tested by test_v3_layers.py::test_daylight_follows_the_sun."""
        super().__init__(spec, ctx, index)
        at = self.opts["at"]
        if at not in ("timeline", "now"):
            try:
                parse_date(at)
            except ValueError as exc:
                raise RecipeError(f"daylight at must be 'timeline', 'now' or an ISO date-time, not {at!r}") from exc

    def _image(self, ref: str) -> np.ndarray:
        """Return a named basemap or an image file as linear RGB at output size. Tested indirectly by test_v3_layers.py::test_daylight_follows_the_sun."""
        w, h = self.ctx.width, self.ctx.height
        path = fetch_basemap(ref, w, self.ctx.refresh) if ref in BASEMAPS else self.ctx.input_path(ref)
        return resample_equirect(srgb_to_linear(load_rgb(path)), w, h)

    def prepare(self) -> None:
        """Store the day and night images and, for 'now', freeze the build time so every frame and worker agree. Tested by test_v3_layers.py::test_daylight_follows_the_sun."""
        o = self.opts
        self.store.put("day", (self._image(o["day"]) * np.float32(o["day_brightness"])).astype(np.float16))
        self.store.put("night", (self._image(o["night"]) * np.float32(o["night_brightness"])).astype(np.float16))
        fixed = datetime.now(timezone.utc).isoformat() if o["at"] == "now" else (None if o["at"] == "timeline" else o["at"])
        self.store.put_json("meta", {"at": fixed})

    def attach(self) -> None:
        """Memory-map both images and precompute the latitude/longitude trigonometry. Tested indirectly by test_v3_layers.py::test_daylight_follows_the_sun."""
        self.day, self.night = self.store.get("day"), self.store.get("night")
        fixed = self.store.get_json("meta")["at"]
        self.fixed = datetime.fromisoformat(fixed) if fixed else None
        lon, lat = lon_lat_axes(self.ctx.width, self.ctx.height)
        self.slat, self.clat = np.sin(lat).astype(np.float32), np.cos(lat).astype(np.float32)
        self.lon = lon

    def when(self, t) -> datetime:
        """Return the moment whose sunlight is drawn: the fixed time, or the frame's date. Tested by test_v3_layers.py::test_daylight_follows_the_sun."""
        if self.fixed:
            return self.fixed if self.fixed.tzinfo else self.fixed.replace(tzinfo=timezone.utc)
        if t.date is None:
            raise RecipeError("daylight at = 'timeline' needs [animation] start and end dates (or set at = 'now')")
        return t.date

    def render(self, canvas: np.ndarray, t) -> None:
        """Replace the canvas with night, day and the twilight blend between them, in row strips. Tested by test_v3_layers.py::test_daylight_follows_the_sun."""
        sun_lat, sun_lon = (math.radians(v) for v in subsolar_point(self.when(t)))
        cdl = np.cos(self.lon - sun_lon).astype(np.float32)[None, :]
        tw = math.radians(max(self.opts["twilight_deg"], 0.1))
        for y0 in range(0, self.ctx.height, 128):
            y1 = min(self.ctx.height, y0 + 128)
            sin_el = self.clat[y0:y1, None] * np.float32(math.cos(sun_lat)) * cdl + self.slat[y0:y1, None] * np.float32(math.sin(sun_lat))
            f = np.clip((np.arcsin(np.clip(sin_el, -1, 1)) + tw / 2) / tw, 0.0, 1.0)
            f = (f * f * (3 - 2 * f))[..., None]
            night = self.night[y0:y1].astype(np.float32)
            canvas[y0:y1] = night + (self.day[y0:y1].astype(np.float32) - night) * f

    def values(self, t) -> dict:
        """Return the moment drawn and the subsolar point for HUD templates. Tested by test_v3_layers.py::test_daylight_follows_the_sun."""
        when = self.when(t)
        lat, lon = subsolar_point(when)
        return {"sun_time": when, "sun_lat": lat, "sun_lon": lon}

    def credits(self) -> list[str]:
        """Return the NASA/GIBS credits for any named basemaps used. Tested indirectly by test_v3_layers.py::test_daylight_follows_the_sun."""
        return [basemap_credit(n) for n in dict.fromkeys((self.opts["day"], self.opts["night"])) if n in BASEMAPS]
