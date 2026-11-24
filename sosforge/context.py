"""Build context shared by layers, and the on-disk field store that worker processes memory-map."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .fetch import ensure_asset
from .recipe import Recipe, RecipeError
from .timeline import Timeline


@dataclass
class BuildContext:
    """Resolution, timing, folders and switches for one build, readable by every layer. Tested indirectly by test_build.py."""

    recipe: Recipe
    width: int
    timeline: Timeline
    build_dir: Path
    workers: int = 1
    refresh: bool = False

    @property
    def height(self) -> int:
        """Return the output height implied by the strict 2:1 projection. Tested indirectly by test_build.py::test_texture_build_end_to_end."""
        return self.width // 2

    def input_path(self, ref: str) -> Path:
        """Resolve a layer input that names a recipe [assets] entry or a recipe-relative file path. Tested by test_layers.py::test_basemap_from_path_and_color."""
        if ref in self.recipe.assets:
            return ensure_asset(self.recipe.assets[ref], force=False)
        path = self.recipe.resolve(ref)
        if not path.exists():
            raise RecipeError(f"input '{ref}' is neither an [assets] name nor an existing file ({path})")
        return path

    def to_args(self) -> dict:
        """Return plain constructor arguments so worker processes can rebuild an identical context. Tested indirectly by test_build.py::test_movie_build_end_to_end."""
        return {"recipe": str(self.recipe.path), "width": self.width, "frames": self.timeline.frames,
                "fps": self.timeline.fps, "build_dir": str(self.build_dir), "workers": self.workers,
                "hud_mode": self.recipe.hud["mode"]}


class FieldStore:
    """A per-layer cache folder of .npy arrays and JSON metadata with an atomic completion marker. Tested by test_layers.py::test_field_store_round_trip."""

    def __init__(self, root: Path):
        """Create the store folder if needed. Tested by test_layers.py::test_field_store_round_trip."""
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, name: str, arr: np.ndarray) -> None:
        """Save an array through a temporary file so interrupted builds never leave truncated data. Tested by test_layers.py::test_field_store_round_trip."""
        tmp = self.root / f"{name}.tmp.npy"
        np.save(tmp, arr)
        tmp.replace(self.root / f"{name}.npy")

    def get(self, name: str, mmap: bool = True) -> np.ndarray:
        """Load an array, memory-mapped by default so all workers share one page-cached copy. Tested by test_layers.py::test_field_store_round_trip."""
        return np.load(self.root / f"{name}.npy", mmap_mode="r" if mmap else None)

    def has(self, name: str) -> bool:
        """Return True when an array with this name exists. Tested by test_layers.py::test_field_store_round_trip."""
        return (self.root / f"{name}.npy").exists()

    def put_json(self, name: str, obj) -> None:
        """Write JSON metadata atomically. Tested by test_layers.py::test_field_store_round_trip."""
        tmp = self.root / f"{name}.tmp.json"
        tmp.write_text(json.dumps(obj, indent=1, default=str))
        tmp.replace(self.root / f"{name}.json")

    def get_json(self, name: str):
        """Read JSON metadata. Tested by test_layers.py::test_field_store_round_trip."""
        return json.loads((self.root / f"{name}.json").read_text())

    @property
    def complete(self) -> bool:
        """Return True once prepare() finished for this exact layer configuration. Tested by test_layers.py::test_field_store_round_trip."""
        return (self.root / "COMPLETE").exists()

    def mark_complete(self) -> None:
        """Record that prepare() finished so later builds can skip it. Tested by test_layers.py::test_field_store_round_trip."""
        (self.root / "COMPLETE").write_text("ok\n")
