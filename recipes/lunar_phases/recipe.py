"""Lunar-specific hooks: phase names and day for the HUD, the Earth-view phase icon, and landmark checks that prove
the SVS maps are centred on 0 deg, east-positive and co-registered."""
from __future__ import annotations

import math

import numpy as np

from sosforge import Check, register_icon
from sosforge.grid import cap_mean, linear_to_srgb, lon_lat_axes, luminance

SYNODIC_DAYS = 29.530589
PRINCIPAL = ("New Moon", "First Quarter", "Full Moon", "Third Quarter")
INTERMEDIATE = ("Waxing Crescent", "Waxing Gibbous", "Waning Gibbous", "Waning Crescent")
HALF_WINDOW_DAYS = 1.0
GLYPH_LIT = np.array([238, 234, 222], np.float32)
GLYPH_DARK = np.array([50, 54, 64], np.float32)


def phase_name(day: float) -> str:
    """Name the phase, using a principal name within one day of its instant and an intermediate name otherwise. Tested by test_lunar_recipe.py::test_values_and_phase_names."""
    d = day % SYNODIC_DAYS
    q = SYNODIC_DAYS / 4.0
    for i, name in enumerate(PRINCIPAL):
        delta = abs(d - i * q)
        if min(delta, SYNODIC_DAYS - delta) <= HALF_WINDOW_DAYS:
            return name
    return INTERMEDIATE[int(d // q) % 4]


def illuminated_fraction(day: float) -> float:
    """Return the illuminated fraction of the Earth-facing disc for a lunar age in days. Tested by test_lunar_recipe.py::test_values_and_phase_names."""
    return 0.5 * (1.0 - math.cos(2.0 * math.pi * day / SYNODIC_DAYS))


def values(t, ctx) -> dict:
    """Return day, phase and lit fraction for HUD templates from the loop fraction. Tested by test_lunar_recipe.py::test_values_and_phase_names."""
    day = SYNODIC_DAYS * t.fraction
    return {"day": day, "phase": phase_name(day), "lit": illuminated_fraction(day)}


@register_icon("moon_phase")
def moon_icon(size: int, vals: dict) -> np.ndarray:
    """Paint the phase as seen from Earth (lunar east on the right) as an anti-aliased RGBA disc. Tested by test_lunar_recipe.py::test_moon_icon_waxing_lit_on_right."""
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32)
    x, y = (xx + 0.5) / size * 2 - 1, 1 - (yy + 0.5) / size * 2
    r = np.sqrt(x * x + y * y)
    z = np.sqrt(np.clip(1 - r * r, 0, 1))
    lon = math.radians(vals["sun_lon_deg"])
    dot = x * math.sin(lon) + z * math.cos(lon)
    lit = np.clip(dot / 0.05 + 0.5, 0, 1)[..., None]
    rgb = GLYPH_DARK + (GLYPH_LIT - GLYPH_DARK) * lit
    alpha = np.clip((1 - r) * size * 0.5 + 0.5, 0, 1) * 255
    return np.dstack([rgb, alpha]).astype(np.uint8)


def checks(ctx, layers) -> list[Check]:
    """Verify the Moon maps with landmarks calibrated on the SVS data: near side darker, Crisium east, maria low. Tested by test_lunar_recipe.py::test_landmark_checks_on_real_maps (skipped without the SVS files)."""
    globe = next(l for l in layers if l.TYPE == "lit_globe")
    albedo = np.asarray(globe.store.get("albedo"))
    height = np.asarray(globe.store.get("height"))
    return landmark_checks(albedo, height)


def landmark_checks(albedo_lin: np.ndarray, height_m: np.ndarray) -> list[Check]:
    """Run the four orientation/co-registration checks on linear albedo and metre heights at any resolution. Tested by test_lunar_recipe.py::test_landmark_checks_on_real_maps."""
    lum = luminance(linear_to_srgb(albedo_lin))
    h_px, w_px = lum.shape
    lon, lat = lon_lat_axes(w_px, h_px)
    band = np.abs(lat) < math.radians(60)
    near = np.abs(lon) < np.pi / 2
    nf = lum[band][:, near].mean() / lum[band][:, ~near].mean()
    ew = lum[band][:, (lon > 0) & near].mean() / lum[band][:, (lon < 0) & near].mean()
    cris = cap_mean(lum, 17.0, 59.0, 11.0, 15.0) / max(cap_mean(lum, 17.0, 59.0, 0.0, 4.0), 1e-6)
    m = band[:, None] & near[None, :]
    corr = float(np.corrcoef(lum[m], height_m[m])[0, 1])
    return [
        Check("Moon: 0 deg centred (near-side maria darker than far side)", nf < 0.92, f"near/far {nf:.3f} (SVS 0.83)"),
        Check("Moon: east positive (Mare Crisium rim/centre at 17N 59E)", cris > 1.45, f"{cris:.2f} (SVS 1.66, mirrored 1.31)"),
        Check("Moon: east positive (near-east brighter than Procellarum)", ew > 1.04, f"{ew:.3f} (SVS 1.11)"),
        Check("Moon: albedo/elevation co-registered (maria are low)", corr > 0.25, f"corr {corr:+.2f} (SVS +0.44)"),
    ]
