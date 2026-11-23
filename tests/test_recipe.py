"""Tests for recipe parsing, validation messages and hook loading."""
from __future__ import annotations

import pytest

from conftest import write_recipe
from sosforge.recipe import RecipeError, load_recipe
from sosforge.registry import LAYERS

MIN = """
[dataset]
name = "Test Dataset!"
[[layers]]
type = "basemap"
color = "#000000"
"""


def test_load_recipe_defaults_and_assets(tmp_path):
    """Checks defaults fill in, the slug derives from the name, assets parse with default filenames, and texture is the default. Covers load_recipe, with_defaults, slugify and Asset.from_spec; this test is not itself tested."""
    d = write_recipe(tmp_path / "r", MIN + '\n[assets.dem]\nurl = "https://x.org/a/b/ldem.tif"\nsize = 10\n')
    r = load_recipe(d)
    assert r.slug == "test_dataset" and r.dataset["stem"] == "test_dataset" and not r.is_movie
    assert r.hud["anchors"] == [0.0, 180.0] and r.animation["fps"] == 30 and r.encode["codecs"] == ["mpeg4", "h264"]
    assert r.assets["dem"].filename == "ldem.tif" and r.assets["dem"].size == 10
    assert r.dir == d.resolve() and r.resolve("data/x.csv") == d.resolve() / "data" / "x.csv"


@pytest.mark.parametrize("snippet,msg", [
    ('[datset]\nname="x"', "did you mean 'dataset'"),
    ('[dataset]\nname="x"\nwidht=1024\n[[layers]]\ntype="basemap"\ncolor="#000000"', "did you mean 'width'"),
    ('[dataset]\nname="x"\n[[layers]]\ntype="pionts"', "did you mean 'points'"),
    ('[dataset]\nname="x"\noutput="video"\n[[layers]]\ntype="basemap"\ncolor="#000000"', "texture' or 'movie"),
    ('[dataset]\nname="x"\nwidth=1002\n[[layers]]\ntype="basemap"\ncolor="#000000"', "multiple of 4"),
    ('[dataset]\nname="x"', "at least one"),
    ('[dataset]\nname="x"\n[playlist]\ndata="y"\n[[layers]]\ntype="basemap"\ncolor="#000000"', "generated from"),
    ('[dataset]\nname = "x"\n[[layers]]\ntype = "basemap"\ncolour = "#000"', "did you mean 'color'"),
    ('[dataset]\nname="x"\n[encode]\ncodec="h264"\n[[layers]]\ntype="basemap"\ncolor="#000000"', "did you mean 'codecs'"),
    ('[dataset]\nname="x"\n[encode]\ncodecs=["h265"]\n[[layers]]\ntype="basemap"\ncolor="#000000"', "codecs must be"),
    ('[dataset]\nname="x"\n[encode]\ncodecs=[]\n[[layers]]\ntype="basemap"\ncolor="#000000"', "codecs must be"),
    ('[dataset]\nname="x"\n[encode]\nbitrate="10M"\n[[layers]]\ntype="basemap"\ncolor="#000000"', "at least 25M"),
    ('[dataset]\nname="x"\n[hud]\nmode="both"\n[[layers]]\ntype="basemap"\ncolor="#000000"', "mode must be"),
    ('[dataset]\nname="x"\n[playlist]\nlabel="x.txt"\n[[layers]]\ntype="basemap"\ncolor="#000000"', "generated from \\[hud\\] mode"),
])
def test_unknown_keys_suggest_fix(tmp_path, snippet, msg):
    """Checks typos and invalid values produce errors that name the fix. Covers check_keys and load_recipe validation (layer options via build); this test is not itself tested."""
    d = write_recipe(tmp_path / "r", snippet)
    with pytest.raises(RecipeError, match=msg):
        r = load_recipe(d)
        from sosforge.build import BuildOptions, make_context, make_layers
        make_layers(make_context(r, BuildOptions()))


def test_hook_registers_layer_and_values(tmp_path):
    """Checks recipe.py can register a custom layer and supply HUD values that templates use. Covers _load_hooks, register_layer, Recipe.hook and FrameRenderer.values; this test is not itself tested."""
    hook = '''
from sosforge import Layer, register_layer
@register_layer("test_stripes")
class Stripes(Layer):
    """Paints a constant. Test helper."""
    OPTIONS = {"level": 0.5}
    def render(self, canvas, t):
        """Fill. Test helper."""
        canvas[...] = self.opts["level"]
def values(t, ctx):
    """Return a value. Test helper."""
    return {"answer": 42}
'''
    d = write_recipe(tmp_path / "r", '[dataset]\nname="Hooked"\nwidth=128\n[[layers]]\ntype="test_stripes"\nlevel=0.25\n'
                     '[hud]\ntitle="A{answer}"\n', {"recipe.py": hook})
    r = load_recipe(d)
    assert "test_stripes" in LAYERS and r.hook("values")(None, None) == {"answer": 42}
    from sosforge.build import BuildOptions, FrameRenderer, make_context, make_layers, prepare_layers
    ctx = make_context(r, BuildOptions(build_dir=tmp_path / "b"))
    layers = make_layers(ctx)
    prepare_layers(ctx, layers)
    fr = FrameRenderer(ctx, layers)
    assert fr.values(ctx.timeline.at(0))["answer"] == 42 and fr.frame(0).shape == (64, 128, 3)
