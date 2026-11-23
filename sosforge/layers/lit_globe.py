"""Lit globe: an albedo map shaded by a moving Sun with DEM relief normals and horizon-map cast shadows.

Used by the lunar_phases recipe, and usable for any body with a colour map and an elevation map (set radius_km).
The surface never moves; the subsolar point travels sun_cycles times around the equator over the animation.
"""
from __future__ import annotations

import math
import multiprocessing as mp
import os

import numpy as np

from .. import relief
from ..basemaps import load_rgb
from ..checks import Check
from ..grid import lon_lat_axes, lon_to_x, luminance, resample_equirect, seam_ratio, srgb_to_linear
from ..recipe import REQUIRED, RecipeError
from ..registry import register_layer
from .base import Layer

UNITS = {"km": (1000.0, 0.0), "m": (1.0, 0.0), "svs_uint16": (0.5, -10000.0)}


def _horizon_task(args: tuple) -> np.ndarray:
    """Pool task computing one bearing's horizon map from the stored height field. Tested indirectly by test_lunar_recipe.py::test_lunar_preview_build."""
    import cv2
    cv2.setNumThreads(1)
    height_path, bearing, relief_x, width, radius_m, max_km = args
    h = np.load(height_path, mmap_mode="r").astype(np.float32) * np.float32(relief_x)
    return relief.horizon_map(h, bearing, relief.horizon_distances(width, radius_m, max_km), radius_m)


def load_elevation(path, units: str, scale: float | None, offset_m: float) -> np.ndarray:
    """Read a DEM raster and convert it to metres with named units or an explicit scale and offset. Tested by test_layers.py::test_lit_globe_elevation_units."""
    import tifffile
    from PIL import Image
    path = str(path)
    if path.lower().endswith((".tif", ".tiff")):
        arr = tifffile.imread(path)
    else:
        with Image.open(path) as im:
            arr = np.asarray(im)
    arr = arr[..., 0] if arr.ndim == 3 else arr
    if units not in UNITS:
        raise RecipeError(f"elevation_units must be one of {', '.join(UNITS)}")
    s, off = UNITS[units]
    return (arr.astype(np.float32) * np.float32(scale if scale is not None else s) + np.float32(off + offset_m))


@register_layer("lit_globe")
class LitGlobeLayer(Layer):
    """Sunlit planetary surface with relief shading and cast shadows. Tested by test_layers.py and test_lunar_recipe.py."""

    OPTIONS = {"color": REQUIRED, "elevation": REQUIRED, "radius_km": REQUIRED, "elevation_units": "km",
               "elevation_scale": None, "elevation_offset_m": 0.0, "relief": 1.0, "shadows": True,
               "shadow_max_km": 270.0, "ambient": 0.025, "brdf": "lambert", "sun_start_lon_deg": 180.0,
               "sun_cycles": 1.0, "sun_direction": "west", "sun_lat_deg": 0.0, "credit": None}

    def __init__(self, spec, ctx, index):
        """Validate BRDF and sun direction choices. Tested by test_layers.py::test_layer_option_validation."""
        super().__init__(spec, ctx, index)
        if self.opts["brdf"] not in ("lambert", "lommel-seeliger"):
            raise RecipeError("lit_globe brdf must be 'lambert' or 'lommel-seeliger'")
        if self.opts["sun_direction"] not in ("west", "east"):
            raise RecipeError("lit_globe sun_direction must be 'west' or 'east'")
        self.radius_m = float(self.opts["radius_km"]) * 1000.0

    def inputs(self) -> dict:
        """Fingerprint the colour and elevation inputs by asset name or file size and mtime. Tested indirectly by test_build.py::test_cache_reused_and_invalidated."""
        out = {}
        for k in ("color", "elevation"):
            ref = self.opts[k]
            a = self.ctx.recipe.assets.get(ref)
            out[k] = (a.sha256 or a.url) if a else f"{ref}:{self.ctx.recipe.resolve(ref).stat().st_size}"
        return out

    def sun_lon(self, fraction: float) -> float:
        """Return the subsolar longitude in radians at an animation fraction, wrapped to [-pi, pi). Tested by test_layers.py::test_lit_globe_sun_schedule_and_centroid."""
        sign = -1.0 if self.opts["sun_direction"] == "west" else 1.0
        deg = self.opts["sun_start_lon_deg"] + sign * 360.0 * self.opts["sun_cycles"] * fraction
        return math.radians((deg + 180.0) % 360.0 - 180.0)

    def prepare(self) -> None:
        """Resample albedo and elevation to the output grid, build normals, and ray-march horizon maps in parallel. Tested by test_lunar_recipe.py::test_lunar_preview_build."""
        w, h = self.ctx.width, self.ctx.height
        o = self.opts
        albedo = resample_equirect(srgb_to_linear(load_rgb(self.ctx.input_path(o["color"]))), w, h)
        height = resample_equirect(load_elevation(self.ctx.input_path(o["elevation"]), o["elevation_units"],
                                                  o["elevation_scale"], o["elevation_offset_m"]), w, h)
        self.store.put("albedo", albedo)
        self.store.put("height", height)
        self.store.put("normals", relief.surface_normals(height, self.radius_m, o["relief"]))
        if o["shadows"]:
            tasks = [(str(self.store.root / "height.npy"), float(b), o["relief"], w, self.radius_m, o["shadow_max_km"])
                     for b in relief.bearings()]
            n = max(1, min(self.ctx.workers * 2, relief.N_BEARINGS, os.cpu_count() or 1))
            if n == 1:
                maps = [_horizon_task(t) for t in tasks]
            else:
                with mp.get_context("spawn").Pool(n) as pool:
                    maps = pool.map(_horizon_task, tasks)
            self.store.put("horizons", np.stack(maps))

    def attach(self) -> None:
        """Memory-map albedo, normals and horizons and precompute the grid axes. Tested indirectly by test_lunar_recipe.py::test_lunar_preview_build."""
        self.albedo = self.store.get("albedo")
        self.normals = self.store.get("normals")
        self.horizons = self.store.get("horizons") if self.opts["shadows"] and self.store.has("horizons") else None
        self.lon, self.lat = lon_lat_axes(self.ctx.width, self.ctx.height)

    def shading(self, y0: int, y1: int, sun_lon: float, ambient: float | None = None, shadows: bool = True) -> np.ndarray:
        """Return the irradiance factor for rows y0..y1 at a subsolar longitude. Tested by test_layers.py::test_lit_globe_sun_schedule_and_centroid."""
        return relief.shading_rows(self.normals, self.horizons if shadows else None, self.lat, self.lon, y0, y1,
                                   sun_lon, math.radians(self.opts["sun_lat_deg"]),
                                   self.opts["ambient"] if ambient is None else ambient, self.opts["brdf"])

    def render(self, canvas: np.ndarray, t) -> None:
        """Replace the canvas with the sunlit surface, processing 128-row strips to bound memory. Tested by test_lunar_recipe.py::test_lunar_preview_build."""
        sun = self.sun_lon(t.fraction)
        for y0 in range(0, self.ctx.height, 128):
            y1 = min(self.ctx.height, y0 + 128)
            canvas[y0:y1] = self.albedo[y0:y1] * self.shading(y0, y1, sun)[..., None]

    def values(self, t) -> dict:
        """Return the subsolar longitude in degrees for HUD templates and icons. Tested by test_lunar_recipe.py::test_values_and_phase_names."""
        return {"sun_lon_deg": math.degrees(self.sun_lon(t.fraction))}

    def centroid_x(self, sun_lon: float) -> float:
        """Return the circular-mean x pixel of irradiance for a subsolar longitude without ambient or shadows. Tested by test_layers.py::test_lit_globe_sun_schedule_and_centroid."""
        col = np.zeros(self.ctx.width)
        weights = np.cos(self.lat)
        for y0 in range(0, self.ctx.height, 256):
            y1 = min(self.ctx.height, y0 + 256)
            col += (self.shading(y0, y1, sun_lon, ambient=0.0, shadows=False) * weights[y0:y1, None]).sum(axis=0)
        ang = math.atan2(float((col * np.sin(self.lon)).sum()), float((col * np.cos(self.lon)).sum()))
        return float(lon_to_x(ang, self.ctx.width))

    def checks(self) -> list[Check]:
        """Verify 2:1 inputs, seamless wrap, and that sun-at-0 lights X = W/2 while sun-at-180 lights the edges. Tested by test_layers.py::test_lit_globe_sun_schedule_and_centroid."""
        w = self.ctx.width
        alb, hgt = np.asarray(self.albedo), np.asarray(self.store.get("height"))
        tol = max(1.0, w / 1440.0)
        x0 = self.centroid_x(0.0)
        x180 = self.centroid_x(math.pi) % w
        return [
            Check("lit_globe seamless +/-180 (albedo)", seam_ratio(luminance(alb)) < 2.5, f"edge/interior {seam_ratio(luminance(alb)):.2f}"),
            Check("lit_globe seamless +/-180 (elevation)", seam_ratio(hgt) < 2.5, f"edge/interior {seam_ratio(hgt):.2f}"),
            Check("prime meridian at X = width/2 (sun at 0 deg irradiance centroid)", abs(x0 - w / 2) <= tol,
                  f"centroid X = {x0:.2f}, expected {w / 2:.1f} +/- {tol:.1f}"),
            Check("+/-180 at the left/right edge (sun at 180 deg irradiance centroid)", min(x180, w - x180) <= tol,
                  f"centroid X = {x180:.2f}, expected 0 == {w} +/- {tol:.1f}"),
        ]

    def credits(self) -> list[str]:
        """Return the recipe-provided credit line for the surface maps. Tested indirectly by test_lunar_recipe.py::test_lunar_preview_build."""
        return [self.opts["credit"]] if self.opts["credit"] else []
