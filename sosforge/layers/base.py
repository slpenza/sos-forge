"""Layer base class: validated options, a cache key, and the prepare/attach/render/overlay lifecycle.

prepare() runs once in the main process and writes heavy arrays to self.store; attach() runs in every
worker and memory-maps them; render() adds to the linear-light canvas; overlay() draws on the final sRGB frame.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np

from ..checks import Check
from ..context import BuildContext, FieldStore
from ..recipe import with_defaults
from ..timeline import FrameTime


class Layer:
    """Base class for every recipe layer type. Tested by test_layers.py and indirectly by every build test."""

    TYPE = "layer"
    VERSION = 1
    OPTIONS: dict = {}

    def __init__(self, spec: dict, ctx: BuildContext, index: int):
        """Validate the layer's TOML options against OPTIONS and keep the build context. Tested by test_layers.py::test_layer_option_validation."""
        spec = {k: v for k, v in spec.items() if k != "type"}
        self.opts = with_defaults(f"layers #{index + 1} ({self.TYPE})", spec, self.OPTIONS)
        self.ctx, self.index = ctx, index
        self.store: FieldStore | None = None

    def inputs(self) -> dict:
        """Return fingerprints of external inputs so changed data invalidates the cache. Tested indirectly by test_build.py::test_cache_reused_and_invalidated."""
        return {}

    def key(self) -> str:
        """Return a hash of type, version, options, inputs and resolution that names this layer's cache folder. Tested by test_build.py::test_cache_reused_and_invalidated."""
        blob = json.dumps({"type": self.TYPE, "v": self.VERSION, "opts": self.opts, "inputs": self.inputs(),
                           "w": self.ctx.width}, sort_keys=True, default=str)
        return hashlib.sha1(blob.encode()).hexdigest()[:12]

    def bind(self, path: Path | None = None) -> FieldStore:
        """Attach this layer to its cache folder, computing the key unless a worker passes the path. Tested indirectly by test_build.py::test_movie_build_end_to_end."""
        self.store = FieldStore(path or self.ctx.build_dir / f"L{self.index}_{self.TYPE}_{self.key()}")
        return self.store

    def prepare(self) -> None:
        """Compute and store heavy per-resolution data once in the main process. Overridden by subclasses; the base no-op is not tested."""

    def attach(self) -> None:
        """Load stored data in a render process. Overridden by subclasses; the base no-op is not tested."""

    def render(self, canvas: np.ndarray, t: FrameTime) -> None:
        """Draw into the linear-light float32 canvas in place. Overridden by subclasses; the base no-op is not tested."""

    def overlay(self, frame: np.ndarray, t: FrameTime) -> None:
        """Draw onto the final uint8 sRGB frame in place, for flat panels and labels. Overridden by subclasses; the base no-op is not tested."""

    def values(self, t: FrameTime) -> dict:
        """Return HUD template values for a frame. Overridden by subclasses; the base empty dict is not tested."""
        return {}

    def checks(self) -> list[Check]:
        """Return verification results for the prepared data. Overridden by subclasses; the base empty list is not tested."""
        return []

    def credits(self) -> list[str]:
        """Return attribution lines that must travel with the dataset. Overridden by subclasses; the base empty list is not tested."""
        return []
