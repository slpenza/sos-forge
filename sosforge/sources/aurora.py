"""NOAA Space Weather Prediction Center OVATION aurora forecast: a 1-degree global grid of aurora probability (0-100)
for roughly the next hour, refreshed every few minutes. US Government data, public domain; credit NOAA SWPC."""
from __future__ import annotations

import json
import logging
from datetime import datetime

import numpy as np

from ..fetch import cached_call, http_request
from ..recipe import RecipeError, check_keys
from ..registry import register_source
from .base import GridField, digest_bytes

LOG = logging.getLogger("sosforge.aurora")
URL = "https://services.swpc.noaa.gov/json/ovation_aurora_latest.json"
ATTRIBUTION = "NOAA Space Weather Prediction Center, OVATION aurora forecast"


def parse_ovation(doc: dict) -> GridField:
    """Turn an OVATION JSON document into a GridField with its observation and forecast times. Tested by test_v3_sources.py::test_swpc_aurora_parses_grid."""
    pts = np.asarray(doc.get("coordinates") or [], dtype=float)
    if pts.ndim != 2 or pts.shape[1] != 3 or len(pts) == 0:
        raise RecipeError(f"unexpected OVATION payload (keys: {', '.join(doc)})")
    lons, lats = np.unique(pts[:, 0]), np.unique(pts[:, 1])
    grid = np.zeros((len(lats), len(lons)), np.float32)
    grid[np.searchsorted(lats, pts[:, 1]), np.searchsorted(lons, pts[:, 0])] = pts[:, 2]
    times = {}
    for key, name in (("Observation Time", "observation_time"), ("Forecast Time", "forecast_time")):
        if doc.get(key):
            times[name] = datetime.fromisoformat(doc[key].replace("Z", "+00:00"))
    return GridField(grid, float(lons[0]), float(lons[1] - lons[0]), float(lats[0]), float(lats[1] - lats[0]), times,
                     ATTRIBUTION, "Public domain (US Government)")


@register_source("swpc_aurora")
def swpc_aurora(opts: dict, ctx) -> GridField:
    """Fetch the latest OVATION forecast (cached for max_age_minutes, default 30) and return it as a grid. Tested by test_v3_sources.py::test_swpc_aurora_parses_grid with stubbed HTTP; the live feed is exercised by real builds."""
    check_keys("source swpc_aurora", opts, {"url", "max_age_minutes"})
    url = opts.get("url", URL)
    path = cached_call("swpc_aurora", {"url": url}, lambda: http_request(url, timeout=60), suffix=".json",
                       max_age_days=float(opts.get("max_age_minutes", 30)) / 1440.0, refresh=ctx.refresh)
    raw = path.read_bytes()
    g = parse_ovation(json.loads(raw))
    g.fingerprint = digest_bytes(raw)
    LOG.info("aurora forecast for %s, peak %.0f%%", g.times.get("forecast_time"), float(g.values.max()))
    return g
