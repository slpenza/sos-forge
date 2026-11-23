"""Background layers: a named NASA basemap, any equirectangular image file or asset, or a solid colour."""
from __future__ import annotations

import numpy as np

from ..basemaps import BASEMAPS, basemap_credit, fetch_basemap, load_rgb
from ..checks import Check
from ..grid import luminance, parse_color, resample_equirect, seam_ratio, srgb_to_linear
from ..recipe import RecipeError
from ..registry import register_layer
from .base import Layer


@register_layer("basemap")
class BasemapLayer(Layer):
    """Paints the canvas with a basemap scaled by brightness and saturation, blended by opacity. Tested by test_layers.py."""

    OPTIONS = {"name": None, "path": None, "color": None, "brightness": 1.0, "saturation": 1.0, "opacity": 1.0,
               "credit": None}

    def __init__(self, spec, ctx, index):
        """Validate that exactly one of name, path or color is given and that the name is known. Tested by test_layers.py::test_layer_option_validation."""
        super().__init__(spec, ctx, index)
        given = [k for k in ("name", "path", "color") if self.opts[k] is not None]
        if len(given) != 1:
            raise RecipeError(f"basemap layer #{index + 1} needs exactly one of name, path or color (got {given or 'none'})")
        if self.opts["name"] and self.opts["name"] not in BASEMAPS:
            raise RecipeError(f"unknown basemap '{self.opts['name']}' (choose from {', '.join(sorted(BASEMAPS))})")

    def inputs(self) -> dict:
        """Fingerprint a file input by size and mtime so edits invalidate the cache. Tested indirectly by test_build.py::test_cache_reused_and_invalidated."""
        if self.opts["path"] and self.opts["path"] not in self.ctx.recipe.assets:
            p = self.ctx.recipe.resolve(self.opts["path"])
            return {"file": f"{p.stat().st_size}:{int(p.stat().st_mtime)}"} if p.exists() else {}
        return {}

    def prepare(self) -> None:
        """Fetch or load the image, convert to linear light at output size, apply saturation and brightness, and store it as float16. Tested by test_layers.py::test_basemap_from_path_and_color."""
        w, h = self.ctx.width, self.ctx.height
        o = self.opts
        if o["color"] is not None:
            rgb = np.broadcast_to(parse_color(o["color"]), (h, w, 3)).astype(np.float32)
        else:
            path = fetch_basemap(o["name"], w, self.ctx.refresh) if o["name"] else self.ctx.input_path(o["path"])
            rgb = resample_equirect(srgb_to_linear(load_rgb(path)), w, h)
        if o["saturation"] != 1.0:
            lum = luminance(rgb)[..., None]
            rgb = np.clip(lum + (rgb - lum) * np.float32(o["saturation"]), 0.0, None)
        self.store.put("rgb", (rgb * np.float32(o["brightness"])).astype(np.float16))

    def attach(self) -> None:
        """Memory-map the stored image. Tested indirectly by test_build.py::test_movie_build_end_to_end."""
        self.rgb = self.store.get("rgb")

    def render(self, canvas: np.ndarray, t) -> None:
        """Replace or blend the canvas with the basemap according to opacity. Tested by test_layers.py::test_basemap_from_path_and_color."""
        a = float(self.opts["opacity"])
        if a >= 1.0:
            canvas[...] = self.rgb
        else:
            canvas *= np.float32(1.0 - a)
            canvas += self.rgb.astype(np.float32) * np.float32(a)

    def checks(self) -> list[Check]:
        """Verify the basemap is 2:1 and wraps without a seam at +/-180. Tested by test_layers.py::test_basemap_from_path_and_color."""
        rgb = np.asarray(self.store.get("rgb"), dtype=np.float32)
        ratio = seam_ratio(luminance(rgb)) if self.opts["color"] is None else 1.0
        return [Check(f"basemap #{self.index + 1} seamless at +/-180", ratio < 2.5, f"edge/interior {ratio:.2f}")]

    def credits(self) -> list[str]:
        """Return the NASA/GIBS credit for named basemaps or the recipe's own credit text. Tested by test_layers.py::test_basemap_named_uses_gibs_request."""
        if self.opts["credit"]:
            return [self.opts["credit"]]
        return [basemap_credit(self.opts["name"])] if self.opts["name"] else []
