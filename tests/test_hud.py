"""Tests for the templated HUD."""
from __future__ import annotations

import numpy as np
import pytest

from sosforge.hud import HudRenderer, render_template, sample_indices
from sosforge.recipe import HUD_KEYS, with_defaults


def spec(**kw) -> dict:
    """Return a HUD spec with defaults plus overrides. Helper; not itself tested."""
    return with_defaults("hud", kw, HUD_KEYS)


def test_fonts_fit_and_scale_with_resolution():
    """Checks font sizes scale with width, long sampled titles shrink to fit, and sampling includes both ends. Covers HudRenderer.__init__, _fit, load_font and sample_indices; this test is not itself tested."""
    s = spec(title="{t}", subtitle="{n:,} places")
    samples = [{"t": "Short", "n": 5}, {"t": "A considerably longer category title", "n": 123456}]
    small, big = HudRenderer(1024, 512, s, samples), HudRenderer(4096, 2048, s, samples)
    assert big.title_size / small.title_size == pytest.approx(4, rel=0.08)
    for r in (small, big):
        assert r.font_title.getlength(samples[1]["t"]) <= r.avail
    assert sample_indices(1000, 10)[0] == 0 and sample_indices(1000, 10)[-1] == 999 and sample_indices(3, 10) == [0, 1, 2]


def test_template_errors_name_missing_key():
    """Checks a missing template key reports the available values. Covers render_template; this test is not itself tested."""
    assert render_template("{a:,} at {d:%Y}", {"a": 1234, "d": __import__("datetime").date(2024, 1, 1)}) == "1,234 at 2024"
    with pytest.raises(KeyError, match="available values are: a"):
        render_template("{missing}", {"a": 1})


def test_unsampled_long_text_still_fits():
    """Checks text longer than any sampled string is shrunk at draw time instead of overflowing the panel. Covers HudRenderer._draw_text; this test is not itself tested."""
    r = HudRenderer(1024, 512, spec(title="{t}"), [{"t": "x"}])
    rgba = r.panel({"t": "An extremely long title that was never sampled during font fitting at all", "fraction": 0})
    alpha_cols = np.nonzero(rgba[..., 3].max(axis=0) > 0)[0]
    assert alpha_cols.max() < r.panel_w


def test_panel_is_premultiplied():
    """Checks the panel is premultiplied RGBA at panel size and the progress bar draws. Covers HudRenderer.panel and _progress; this test is not itself tested."""
    r = HudRenderer(1024, 512, spec(title="T", progress=True, progress_ticks=[0.5]), [{}])
    rgba = r.panel({"fraction": 0.75})
    assert rgba.shape == (r.panel_h, r.panel_w, 4)
    assert np.all(rgba[..., :3] <= rgba[..., 3:4] + 1e-5) and rgba[..., 3].max() <= 1.0


def test_panels_at_anchors_identical():
    """Checks panels at 0 and 180 deg carry identical pixels and sit at the configured latitude. Covers HudRenderer.composite; this test is not itself tested."""
    w = 1024
    r = HudRenderer(w, w // 2, spec(title="Hello", subtitle="world"), [{}])
    f = r.composite(np.zeros((w // 2, w, 3), np.uint8), {"fraction": 0})
    p0, p1 = r.placements
    assert np.array_equal(f[p0.row0:p0.row1, p0.cols], f[p1.row0:p1.row1, p1.cols])
    ys, _ = np.nonzero(f.max(axis=2))
    assert abs(ys.mean() - (90 + 30) / 180 * (w // 2)) < 4
