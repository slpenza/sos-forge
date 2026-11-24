"""Tests for the v0.3.0 demo recipes and the three-argument values hook."""
from __future__ import annotations

from pathlib import Path

import pytest

from conftest import write_recipe
from sosforge.build import BuildOptions, FrameRenderer, make_context, make_layers, prepare_layers
from sosforge.recipe import load_recipe

RECIPES = Path(__file__).resolve().parents[1] / "recipes"


@pytest.mark.parametrize("slug", ["aurora_forecast", "getting_richer", "cities_growing"])
def test_demo_recipes_validate(slug, tmp_path):
    """Checks each v0.3.0 demo recipe loads, validates its keys and builds its layers without touching the network. Covers the recipe files; their data downloads are exercised by real builds."""
    r = load_recipe(RECIPES / slug)
    ctx = make_context(r, BuildOptions(preview=True, build_dir=tmp_path / "b"))
    assert r.dataset["slug"] == slug and len(make_layers(ctx)) == 2 and not r.warnings


def test_hook_sees_layer_values(tmp_path):
    """Checks a values hook with three parameters receives the layer values gathered so far while a two-parameter hook still works. Covers FrameRenderer.values; this test is not itself tested."""
    toml = ('[dataset]\nname="h"\n[[layers]]\ntype="points"\nsource={type="csv",path="p.csv"}\n[hud]\nenabled=false\n')
    files = {"p.csv": "lat,lon\n0,0\n1,1\n"}
    for name, hook in (("three", "def values(t, ctx, vals):\n    return {'doubled': vals['count'] * 2}\n"),
                       ("two", "def values(t, ctx):\n    return {'doubled': -1}\n")):
        r = load_recipe(write_recipe(tmp_path / name, toml, {**files, "recipe.py": hook}))
        ctx = make_context(r, BuildOptions(width=64, build_dir=tmp_path / name / "b", workers=1))
        layers = make_layers(ctx)
        prepare_layers(ctx, layers)
        assert FrameRenderer(ctx, layers).values(ctx.timeline.at(0))["doubled"] == (4 if name == "three" else -1)


def test_cities_growing_hook():
    """Checks the cities hook words populations and flags years after 2025 as projected. Covers recipes/cities_growing/recipe.py people and values; this test is not itself tested."""
    hooks = load_recipe(RECIPES / "cities_growing").hooks
    assert hooks.people(3.61e9) == "3.6 billion" and hooks.people(8.5e8) == "850 million" and hooks.people(12345) == "12,345"

    class T:
        """Stand-in frame time with only a date year. Helper; not itself tested."""

        def __init__(self, year):
            """Store a fake date carrying the year. Helper; not itself tested."""
            self.date = type("D", (), {"year": year})()

    assert hooks.values(T(2025), None, {"weight_sum": 3.6e9}) == {"people": "3.6 billion", "projected": ""}
    assert hooks.values(T(2026), None, {"weight_sum": 3.7e9})["projected"] == " (projected)"
