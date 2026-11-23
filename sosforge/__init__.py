"""sos-forge: build NOAA Science On a Sphere datasets from open data with declarative recipes.

Public hook API for recipe.py files:
    from sosforge import Layer, Check, register_layer, register_source, register_icon
and optional module-level functions `values(t, ctx) -> dict` and `checks(ctx, layers) -> list[Check]`.
"""
from .checks import Check
from .layers.base import Layer
from .meta import VERSION as __version__
from .registry import register_icon, register_layer, register_source
from .timeline import FrameTime

__all__ = ["Check", "FrameTime", "Layer", "register_icon", "register_layer", "register_source", "__version__"]
