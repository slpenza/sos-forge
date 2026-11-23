"""Presenter HUD: a templated title/subtitle/progress panel, optionally with an icon, repeated at anchor longitudes.

Templates are Python format strings over per-frame values (timeline + layers + recipe hooks), for example
"{date:%B %Y}" or "{count:,} places".  Font sizes are fitted once from sampled frames so text never jumps.
"""
from __future__ import annotations

import math
import string
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .panels import Placement, composite, panel_pixels, premultiply
from .registry import ICONS

FONT_CANDIDATES = {
    "bold": [("/System/Library/Fonts/HelveticaNeue.ttc", 10), ("/System/Library/Fonts/Helvetica.ttc", 1),
             ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 0),
             ("/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf", 0), ("C:/Windows/Fonts/segoeuib.ttf", 0)],
    "regular": [("/System/Library/Fonts/HelveticaNeue.ttc", 0), ("/System/Library/Fonts/Helvetica.ttc", 0),
                ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 0),
                ("/usr/share/fonts/dejavu/DejaVuSans.ttf", 0), ("C:/Windows/Fonts/segoeui.ttf", 0)],
}
PANEL_FILL, PANEL_EDGE = (10, 12, 18, 150), (255, 255, 255, 56)
TITLE_RGB, SUB_RGB = (242, 244, 248), (172, 180, 192)
TRACK_RGBA, FILL_RGBA, TICK_RGBA = (255, 255, 255, 46), (255, 214, 140, 230), (255, 255, 255, 90)
SUPERSAMPLE = 3


def load_font(weight: str, size: float, override: str | None = None) -> ImageFont.FreeTypeFont:
    """Load the first available TrueType face for a weight, falling back to Pillow's bundled scalable font. Tested by test_hud.py::test_fonts_fit_and_scale_with_resolution on the host's fonts."""
    size = max(4, int(size))
    for path, index in ([(override, 0)] if override else []) + FONT_CANDIDATES[weight]:
        if path and Path(path).exists():
            try:
                return ImageFont.truetype(path, size, index=index)
            except OSError:
                continue
    return ImageFont.load_default(size=size)


def render_template(template: str, values: dict) -> str:
    """Format a HUD template against frame values, naming the missing key and the available ones on error. Tested by test_hud.py::test_template_errors_name_missing_key."""
    try:
        return string.Formatter().vformat(template, (), values)
    except KeyError as exc:
        raise KeyError(f"HUD template {template!r} uses {{{exc.args[0]}}} but available values are: "
                       f"{', '.join(sorted(values))}") from None


class HudRenderer:
    """Draws the per-frame panel and composites it at every anchor longitude. Tested by test_hud.py."""

    def __init__(self, width: int, height: int, spec: dict, samples: list[dict]):
        """Size the panel from the output width, fit fonts to the widest sampled strings and precompute placements. Tested by test_hud.py::test_fonts_fit_and_scale_with_resolution."""
        self.width, self.height, self.spec = width, height, spec
        self.panel_w, self.panel_h = panel_pixels(width, spec["lat"], spec["width_deg"], spec["height_deg"])
        self.ss_w, self.ss_h = self.panel_w * SUPERSAMPLE, self.panel_h * SUPERSAMPLE
        p = self.ss_h
        self.margin = 0.16 * p
        self.icon = ICONS.get(spec["icon"]) if spec["icon"] else None
        if spec["icon"] and self.icon is None:
            raise KeyError(f"HUD icon '{spec['icon']}' is not registered (known: {', '.join(sorted(ICONS)) or 'none'})")
        self.icon_px = max(4, int(round(p - 2.0 * self.margin)))
        self.text_x = self.margin + (self.icon_px + 0.14 * p if self.icon else 0.06 * p)
        self.avail = self.ss_w - self.text_x - self.margin
        titles = [render_template(spec["title"], v) for v in samples]
        subs = [render_template(spec["subtitle"], v) for v in samples] if spec["subtitle"] else [""]
        self.title_size = self._fit("bold", 0.33 * p, titles)
        self.sub_size = self._fit("regular", 0.19 * p, subs)
        self.font_title = load_font("bold", self.title_size, spec["font"])
        self.font_sub = load_font("regular", self.sub_size, spec["font"])
        has_sub = bool(spec["subtitle"])
        self.title_baseline = (0.47 if has_sub else 0.60) * p
        self.sub_baseline = 0.71 * p
        self.bar_y, self.bar_h = 0.835 * p, max(1.0, 0.045 * p)
        self.placements = [Placement.build(width, height, float(lon), spec["lat"], spec["width_deg"],
                                           spec["height_deg"], self.panel_w, self.panel_h) for lon in spec["anchors"]]

    def _fit(self, weight: str, size: float, texts: list[str]) -> float:
        """Shrink a starting font size until the widest of the given strings fits the text column with 4% slack. Tested by test_hud.py::test_fonts_fit_and_scale_with_resolution."""
        font = load_font(weight, size, self.spec["font"])
        widest = max((font.getlength(t) for t in texts), default=0.0)
        return size * min(1.0, 0.96 * self.avail / widest) if widest > 0 else size

    def _draw_text(self, draw: ImageDraw.ImageDraw, xy, text: str, font, size: float, weight: str, fill) -> None:
        """Draw left-baseline text, shrinking only this string if an unsampled frame would overflow. Tested by test_hud.py::test_unsampled_long_text_still_fits."""
        if text and font.getlength(text) > self.avail:
            font = load_font(weight, size * self.avail / font.getlength(text) * 0.98, self.spec["font"])
        draw.text(xy, text, font=font, fill=fill, anchor="ls")

    def panel(self, values: dict) -> np.ndarray:
        """Render the flat panel for one frame's values as premultiplied float32 RGBA at panel resolution. Tested by test_hud.py::test_panel_is_premultiplied."""
        img = Image.new("RGBA", (self.ss_w, self.ss_h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        p = self.ss_h
        draw.rounded_rectangle([0, 0, self.ss_w - 1, self.ss_h - 1], radius=0.24 * p, fill=PANEL_FILL,
                               outline=PANEL_EDGE, width=max(1, int(round(0.022 * p))))
        if self.icon:
            icon = np.asarray(self.icon(self.icon_px, values), dtype=np.uint8)
            m = int(round(self.margin))
            img.alpha_composite(Image.fromarray(icon, "RGBA"), (m, m))
        self._draw_text(draw, (self.text_x, self.title_baseline), render_template(self.spec["title"], values),
                        self.font_title, self.title_size, "bold", TITLE_RGB)
        if self.spec["subtitle"]:
            self._draw_text(draw, (self.text_x, self.sub_baseline), render_template(self.spec["subtitle"], values),
                            self.font_sub, self.sub_size, "regular", SUB_RGB)
        if self.spec["progress"]:
            self._progress(draw, float(values["fraction"]))
        return premultiply(np.asarray(img), (self.panel_w, self.panel_h))

    def _progress(self, draw: ImageDraw.ImageDraw, fraction: float) -> None:
        """Draw the progress track, filled portion and tick marks along the bottom of the panel. Tested by test_hud.py::test_panel_is_premultiplied."""
        x0, x1 = self.text_x, self.ss_w - self.margin
        y0, y1 = self.bar_y - self.bar_h / 2.0, self.bar_y + self.bar_h / 2.0
        draw.rounded_rectangle([x0, y0, x1, y1], radius=self.bar_h / 2.0, fill=TRACK_RGBA)
        xf = x0 + (x1 - x0) * min(max(fraction, 0.0), 1.0)
        if xf - x0 > self.bar_h:
            draw.rounded_rectangle([x0, y0, xf, y1], radius=self.bar_h / 2.0, fill=FILL_RGBA)
        for q in self.spec["progress_ticks"]:
            xq = x0 + (x1 - x0) * float(q)
            draw.line([(xq, y0 - self.bar_h), (xq, y1 + self.bar_h)], fill=TICK_RGBA,
                      width=max(1, int(round(self.bar_h * 0.4))))

    def composite(self, frame: np.ndarray, values: dict) -> np.ndarray:
        """Composite the frame's panel onto a uint8 sRGB frame in place at every anchor and return the frame. Tested by test_hud.py::test_panels_at_anchors_identical."""
        rgba = self.panel(values)
        for pl in self.placements:
            composite(frame, rgba, pl)
        return frame


def sample_indices(frames: int, count: int) -> list[int]:
    """Return evenly spaced frame indices, always including the first and last, for font fitting. Tested by test_hud.py::test_fonts_fit_and_scale_with_resolution."""
    if frames <= count:
        return list(range(frames))
    return sorted({int(round(i)) for i in np.linspace(0, frames - 1, count)} | {0, frames - 1})


def polar_safe(lat_deg: float, height_deg: float) -> bool:
    """Return True when a panel at this latitude stays clear of the poles, where gnomonic placement degrades. Tested by test_hud.py::test_fonts_fit_and_scale_with_resolution."""
    return abs(lat_deg) + height_deg / 2.0 < 80.0 and not math.isnan(lat_deg)
