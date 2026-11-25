"""USGS earthquakes: the summary feeds the US Geological Survey refreshes every minute, as points with magnitude and
depth (design/v0/v0.8.9.md). US Government data, public domain; credit the USGS."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

import numpy as np

from ..fetch import cached_call, http_request
from ..recipe import RecipeError, check_keys
from ..registry import register_source
from .base import PointTable, digest_bytes

LOG = logging.getLogger("sosforge.usgs")
URL = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/{feed}.geojson"
LEVELS = ("significant", "4.5", "2.5", "1.0", "all")
SPANS = {"hour": 1 / 24, "day": 1, "week": 7, "month": 30}
ATTRIBUTION = "U.S. Geological Survey, Earthquake Hazards Program (earthquake.usgs.gov)"


def parse_feed(doc: dict) -> tuple[dict[str, np.ndarray], dict, dict]:
    """Read a USGS GeoJSON summary feed into columns (lat, lon, depth_km, mag, strength, place, time), its window (quakes_from to quakes_to, when the feed was made) and its strongest magnitude, keeping events of type earthquake with a magnitude. strength is 10^((mag - 4.5) / 2), so each step up a magnitude is about three times brighter. Tested by test_v3_sources.py::test_usgs_reads_quakes."""
    rows = []
    for f in doc.get("features") or []:
        p, g = f.get("properties") or {}, (f.get("geometry") or {}).get("coordinates") or []
        if p.get("type") != "earthquake" or p.get("mag") is None or len(g) < 2:
            continue
        rows.append((float(g[1]), float(g[0]), float(g[2]) if len(g) > 2 and g[2] is not None else 0.0, float(p["mag"]),
                     p.get("place") or "", datetime.fromtimestamp(p["time"] / 1000, timezone.utc)))
    meta = doc.get("metadata") or {}
    if "generated" not in meta:
        raise RecipeError(f"unexpected USGS feed (keys: {', '.join(doc)})")
    made = datetime.fromtimestamp(meta["generated"] / 1000, timezone.utc)
    lat, lon, depth, mag = (np.array([r[i] for r in rows], float) for i in range(4))
    cols = {"lat": lat, "lon": lon, "depth_km": depth, "mag": mag, "strength": 10.0 ** ((mag - 4.5) / 2.0),
            "place": np.array([r[4] for r in rows], dtype=object),
            "time": np.array([r[5].isoformat() for r in rows], dtype=object)}
    return cols, {"quakes_to": made}, {"strongest": float(mag.max()) if len(mag) else 0.0}


@register_source("usgs_quakes")
def usgs_quakes(opts: dict, ctx) -> PointTable:
    """Fetch a USGS summary feed, such as 4.5_week (cached for max_age_minutes, default 10), and return its earthquakes as points, with quakes_from and quakes_to and the strongest magnitude for HUD templates. Tested by test_v3_sources.py::test_usgs_reads_quakes with stubbed HTTP; the live feed is exercised by building earthquakes_this_week."""
    check_keys("source usgs_quakes", opts, {"feed", "max_age_minutes"})
    feed = str(opts.get("feed", "4.5_week"))
    level, _, span = feed.rpartition("_")
    if level not in LEVELS or span not in SPANS:
        raise RecipeError(f"source usgs_quakes: feed is <level>_<span>, a level from {', '.join(LEVELS)} and a span from "
                          f"{', '.join(SPANS)}, such as 4.5_week")
    url = URL.format(feed=feed)
    path = cached_call("usgs", {"url": url}, lambda: http_request(url, timeout=60), suffix=".json",
                       max_age_days=float(opts.get("max_age_minutes", 10)) / 1440.0,
                       refresh=bool(getattr(ctx, "refresh", False)))
    raw = path.read_bytes()
    cols, times, stats = parse_feed(json.loads(raw))
    if not len(cols["lat"]):
        raise RecipeError(f"the USGS {feed} feed has no earthquakes")
    times["quakes_from"] = times["quakes_to"] - timedelta(days=SPANS[span])
    LOG.info("usgs: %d earthquakes in %s, strongest M%.1f", len(cols["lat"]), feed, stats["strongest"])
    return PointTable(cols, ATTRIBUTION, "Public domain (US Government)", digest_bytes(raw), times=times, stats=stats)
