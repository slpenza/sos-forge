"""Name -> implementation registries that recipes reference from TOML and extend from recipe.py hooks."""
from __future__ import annotations

from typing import Callable

LAYERS: dict[str, type] = {}
SOURCES: dict[str, Callable] = {}
ICONS: dict[str, Callable] = {}


def register_layer(name: str) -> Callable[[type], type]:
    """Return a class decorator that registers a Layer subclass under a TOML type name. Tested by test_recipe.py::test_hook_registers_layer_and_values."""
    def deco(cls: type) -> type:
        """Store the class and stamp its TYPE attribute. Tested by test_recipe.py::test_hook_registers_layer_and_values."""
        cls.TYPE = name
        LAYERS[name] = cls
        return cls
    return deco


def register_source(name: str) -> Callable[[Callable], Callable]:
    """Return a function decorator that registers a data source loader under a TOML source type. Tested by test_sources.py::test_csv_and_geojson_points."""
    def deco(fn: Callable) -> Callable:
        """Store the loader function. Tested by test_sources.py::test_csv_and_geojson_points."""
        SOURCES[name] = fn
        return fn
    return deco


def register_icon(name: str) -> Callable[[Callable], Callable]:
    """Return a function decorator that registers a HUD icon painter under a TOML icon name. Tested by test_lunar_recipe.py::test_moon_icon_waxing_lit_on_right."""
    def deco(fn: Callable) -> Callable:
        """Store the icon painter. Tested by test_lunar_recipe.py::test_moon_icon_waxing_lit_on_right."""
        ICONS[name] = fn
        return fn
    return deco


def ensure_builtins() -> None:
    """Import the built-in layers and sources so their decorators populate the registries. Tested indirectly by every recipe test."""
    from . import layers, sources  # noqa: F401
