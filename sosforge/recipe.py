"""Recipe loading: a recipe.toml (declarative) plus an optional recipe.py (custom layers, icons, values, checks).

See docs/recipe-reference.md for the full schema.
"""
from __future__ import annotations

import difflib
import importlib.util
import re
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType

from .fetch import Asset

REQUIRED = object()
DATASET_KEYS = {"name": REQUIRED, "slug": None, "stem": None, "description": "", "output": "texture",
                "width": 4096, "image_format": "jpg", "credits": [], "license": ""}
ANIMATION_KEYS = {"frames": 720, "fps": 30, "loop": True, "start": None, "end": None}
HUD_KEYS = {"enabled": True, "title": "{name}", "subtitle": "", "icon": None, "progress": False,
            "progress_ticks": [], "anchors": [0.0, 180.0], "lat": -30.0, "width_deg": 40.0, "height_deg": 10.0,
            "font": None, "fit_samples": 96, "mode": "panels"}
CODECS = ("mpeg4", "h264")
# MPEG-4 Part 2 first: NOAA's content guide recommends it and warns H.264 "can cause errors in the SOS software".
# H.264 is also built until the target SOS site confirms which it plays (design/v0/v0.1.0.md).
ENCODE_KEYS = {"codecs": ["mpeg4", "h264"], "crf": 18, "preset": "slow", "bitrate": None}
HOOK_KEYS = {"module": "recipe.py"}
TOP_KEYS = {"dataset", "animation", "hud", "encode", "playlist", "assets", "layers", "hooks"}
SOS_PLAYLIST_KEYS = {"animate", "audio", "category", "creator", "duration", "endframe", "firstdwell", "fps",
                     "keywords", "label", "lastdwell", "layer", "layerdata", "majorcategory", "pip", "pipheight",
                     "pipwidth", "piphorizontal", "pipvertical", "skip", "startframe", "subcategory", "tiltx", "tilty",
                     "tiltz", "timer", "zrotationenable", "zrotation", "zfps", "zrotationangle", "catalog_url",
                     "icons", "labelvisible", "labelvertical", "labelhorizontal", "labelheight", "labelformat",
                     "caption", "captionvisible", "captionvertical", "captionhorizontal", "captionheight", "captionformat",
                     "pipstyle", "pipcoords", "pipalpha", "pipfps", "piptimer", "pipdelay", "pipvisible"}


class RecipeError(ValueError):
    """A recipe problem reported to the user with the offending key and a fix hint. Tested by test_recipe.py::test_unknown_keys_suggest_fix."""


def check_keys(section: str, given: dict, allowed: dict | set) -> None:
    """Raise RecipeError for unknown keys, suggesting the closest valid key. Tested by test_recipe.py::test_unknown_keys_suggest_fix."""
    for key in given:
        if key not in allowed:
            close = difflib.get_close_matches(key, list(allowed), n=1)
            hint = f" (did you mean '{close[0]}'?)" if close else f" (valid: {', '.join(sorted(allowed))})"
            raise RecipeError(f"[{section}] unknown key '{key}'{hint}")


def with_defaults(section: str, given: dict, schema: dict) -> dict:
    """Validate a section against its schema and fill defaults, failing on missing required keys. Tested by test_recipe.py::test_load_recipe_defaults_and_assets."""
    check_keys(section, given, schema)
    out = {}
    for key, default in schema.items():
        if key in given:
            out[key] = given[key]
        elif default is REQUIRED:
            raise RecipeError(f"[{section}] '{key}' is required")
        else:
            out[key] = list(default) if isinstance(default, list) else default
    return out


def slugify(text: str) -> str:
    """Turn a dataset name into a lowercase underscore slug safe for folder names. Tested by test_recipe.py::test_load_recipe_defaults_and_assets."""
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_") or "dataset"


@dataclass
class Recipe:
    """A parsed, validated recipe with its directory, sections, assets and loaded hook module. Tested by test_recipe.py."""

    path: Path
    dataset: dict
    animation: dict
    hud: dict
    encode: dict
    playlist: dict
    assets: dict[str, Asset]
    layers: list[dict]
    hooks: ModuleType | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def dir(self) -> Path:
        """Return the folder that holds recipe.toml, against which relative paths resolve. Tested by test_recipe.py::test_load_recipe_defaults_and_assets."""
        return self.path.parent

    @property
    def slug(self) -> str:
        """Return the dataset folder name used under dist/ and on the SOS machine. Tested by test_recipe.py::test_load_recipe_defaults_and_assets."""
        return self.dataset["slug"]

    @property
    def is_movie(self) -> bool:
        """Return True when the recipe renders an animation rather than a single texture. Tested by test_recipe.py::test_load_recipe_defaults_and_assets."""
        return self.dataset["output"] == "movie"

    def resolve(self, path: str | Path) -> Path:
        """Resolve a recipe-relative path, leaving absolute paths and ~ expansions intact. Tested by test_sources.py::test_csv_and_geojson_points."""
        p = Path(path).expanduser()
        return p if p.is_absolute() else (self.dir / p)

    def hook(self, name: str):
        """Return a callable defined in recipe.py by name, or None when absent. Tested by test_recipe.py::test_hook_registers_layer_and_values."""
        return getattr(self.hooks, name, None) if self.hooks else None


def _load_hooks(recipe_dir: Path, module_file: str, slug: str) -> ModuleType | None:
    """Import recipe.py under a unique module name so its decorators register layers, sources and icons. Tested by test_recipe.py::test_hook_registers_layer_and_values."""
    path = recipe_dir / module_file
    if not path.exists():
        return None
    name = f"sosforge_recipe_{slug}"
    if name in sys.modules and getattr(sys.modules[name], "__file__", None) == str(path):
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def find_recipe(ref: str | Path, search: list[Path] | None = None) -> Path:
    """Resolve a recipe name, folder or recipe.toml path to the recipe.toml file. Tested by test_cli.py::test_new_list_check_and_resolve."""
    p = Path(ref).expanduser()
    if p.is_file():
        return p
    if (p / "recipe.toml").is_file():
        return p / "recipe.toml"
    for root in search or default_search_paths():
        cand = root / str(ref) / "recipe.toml"
        if cand.is_file():
            return cand
    raise RecipeError(f"recipe '{ref}' not found (looked in: {', '.join(str(r) for r in search or default_search_paths())})")


def default_search_paths() -> list[Path]:
    """Return ./recipes followed by the recipes folder shipped beside the package. Tested by test_cli.py::test_new_list_check_and_resolve."""
    return [Path.cwd() / "recipes", Path(__file__).resolve().parents[1] / "recipes"]


def load_recipe(ref: str | Path) -> Recipe:
    """Load, validate and default a recipe, importing its hook module before layer types are checked. Tested by test_recipe.py::test_load_recipe_defaults_and_assets."""
    from .registry import LAYERS, ensure_builtins
    ensure_builtins()
    path = find_recipe(ref).resolve()
    try:
        raw = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as exc:
        raise RecipeError(f"{path}: invalid TOML: {exc}") from exc
    check_keys("recipe", raw, TOP_KEYS)
    dataset = with_defaults("dataset", raw.get("dataset", {}), DATASET_KEYS)
    if "{" in str(dataset["name"]) and not dataset["slug"]:
        raise RecipeError("[dataset] a name that takes build values, like {forecast_time:%d %b %Y}, needs a slug")
    dataset["slug"] = dataset["slug"] or slugify(dataset["name"])
    dataset["stem"] = dataset["stem"] or dataset["slug"]
    if dataset["output"] not in ("texture", "movie"):
        raise RecipeError("[dataset] output must be 'texture' or 'movie'")
    if dataset["image_format"] not in ("jpg", "png"):
        raise RecipeError("[dataset] image_format must be 'jpg' or 'png'")
    if int(dataset["width"]) % 4 or int(dataset["width"]) < 64:
        raise RecipeError("[dataset] width must be a multiple of 4 and >= 64 (height is width/2)")
    animation = with_defaults("animation", raw.get("animation", {}), ANIMATION_KEYS)
    hud = with_defaults("hud", raw.get("hud", {}), HUD_KEYS)
    encode = with_defaults("encode", raw.get("encode", {}), ENCODE_KEYS)
    if hud["mode"] not in ("panels", "labels"):
        raise RecipeError("[hud] mode must be 'panels' (styled panels baked into the frames, SOS labels hidden) or "
                          "'labels' (no baked panels; SOS draws the text as static labels)")
    codecs = encode["codecs"]
    if not isinstance(codecs, list) or not codecs or any(c not in CODECS for c in codecs) or len(set(codecs)) != len(codecs):
        raise RecipeError(f"[encode] codecs must be a non-empty list of distinct values from {list(CODECS)}; the first is the primary")
    if encode["bitrate"] is not None:
        m = re.fullmatch(r"(\d+(?:\.\d+)?)[Mm]", str(encode["bitrate"]))
        if not m or float(m.group(1)) < 25:
            raise RecipeError("[encode] bitrate must look like \"50M\" and be at least 25M (NOAA's MPEG-4 minimum)")
    hooks_cfg = with_defaults("hooks", raw.get("hooks", {}), HOOK_KEYS)
    playlist = dict(raw.get("playlist", {}))
    warnings = [f"[playlist] '{k}' is not a documented SOS playlist key" for k in playlist
                if k not in SOS_PLAYLIST_KEYS]
    for reserved in ("name", "data", "description"):
        if reserved in playlist:
            raise RecipeError(f"[playlist] '{reserved}' is generated from [dataset]; set it there instead")
    for reserved in ("label", "labelvisible"):
        if reserved in playlist:
            raise RecipeError(f"[playlist] '{reserved}' is generated from [hud] mode; set the mode instead")
    assets = {}
    for name, spec in raw.get("assets", {}).items():
        check_keys(f"assets.{name}", spec, {"url", "filename", "size", "sha256", "note"})
        if "url" not in spec:
            raise RecipeError(f"[assets.{name}] 'url' is required")
        assets[name] = Asset.from_spec(name, spec)
    hooks = _load_hooks(path.parent, hooks_cfg["module"], dataset["slug"])
    layers = list(raw.get("layers", []))
    if not layers:
        raise RecipeError("a recipe needs at least one [[layers]] table")
    for i, layer in enumerate(layers):
        if layer.get("type") not in LAYERS:
            close = difflib.get_close_matches(str(layer.get("type")), list(LAYERS), n=1)
            hint = f" (did you mean '{close[0]}'?)" if close else f" (available: {', '.join(sorted(LAYERS))})"
            raise RecipeError(f"[[layers]] #{i + 1}: unknown type '{layer.get('type')}'{hint}")
    return Recipe(path, dataset, animation, hud, encode, playlist, assets, layers, hooks, warnings)
