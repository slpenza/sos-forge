"""NASA FIRMS active fires: what the VIIRS instruments on NOAA-20 and NOAA-21 detected in the last 24 hours (or 48
hours, or 7 days), from the open CSV files FIRMS refreshes about every hour (design/v0/v0.8.8.md). NASA data, free and
open; FIRMS asks for an acknowledgement, which every dataset made from it carries."""
from __future__ import annotations

import csv
import io
import logging
from datetime import datetime, timedelta, timezone

import numpy as np

from ..fetch import cached_call, http_request
from ..recipe import RecipeError, check_keys
from ..registry import register_source
from .base import PointTable, digest_bytes

LOG = logging.getLogger("sosforge.firms")
BASE = "https://firms.modaps.eosdis.nasa.gov/data/active_fire"
# Suomi NPP stopped delivering on 2 November 2026, and MODIS Terra ends on 1 December 2026, so VIIRS on the two
# NOAA satellites is the default.
SATELLITES = {"noaa-20": ("noaa-20-viirs-c2", "J1_VIIRS_C2"), "noaa-21": ("noaa-21-viirs-c2", "J2_VIIRS_C2")}
PERIODS = {"24h": 24, "48h": 48, "7d": 168}   # hours; the files reach further back, so they are cut to this
CONFIDENCE = {"l": 0, "low": 0, "n": 1, "nominal": 1, "h": 2, "high": 2}
# FIRMS asks for its name, ESDIS and a link (www.earthdata.nasa.gov/data/tools/firms, "Acknowledgments").
ATTRIBUTION = ("NASA Fire Information for Resource Management System (FIRMS, https://www.earthdata.nasa.gov/data/tools/firms), "
               "part of NASA's Earth Science Data and Information System (ESDIS): VIIRS 375 m active fires")


def firms_url(satellite: str, period: str) -> str:
    """Return FIRMS's open CSV address for a satellite's global detections over a period. Tested by test_v3_sources.py::test_firms_reads_fires."""
    folder, prefix = SATELLITES[satellite]
    return f"{BASE}/{folder}/csv/{prefix}_Global_{period}.csv"


def parse_firms(text: str, min_confidence: str = "nominal") -> dict[str, list]:
    """Read a FIRMS VIIRS CSV into lists of lat, lon, frp, confidence, daynight, satellite and acquisition time (UTC), keeping detections at or above min_confidence. Tested by test_v3_sources.py::test_firms_reads_fires."""
    floor = CONFIDENCE[min_confidence]
    out: dict[str, list] = {k: [] for k in ("lat", "lon", "frp", "confidence", "daynight", "satellite", "time")}
    for row in csv.DictReader(io.StringIO(text)):
        try:
            conf = CONFIDENCE[row["confidence"].strip().lower()]
            when = datetime.strptime(f"{row['acq_date']} {int(row['acq_time']):04d}", "%Y-%m-%d %H%M").replace(tzinfo=timezone.utc)
            lat, lon, frp = float(row["latitude"]), float(row["longitude"]), float(row["frp"] or 0)
        except (KeyError, ValueError) as exc:
            raise RecipeError(f"unexpected FIRMS row ({exc}): columns are {', '.join(row)}") from exc
        if conf < floor:
            continue
        for k, v in (("lat", lat), ("lon", lon), ("frp", frp), ("confidence", row["confidence"]),
                     ("daynight", row.get("daynight", "")), ("satellite", row.get("satellite", "")), ("time", when)):
            out[k].append(v)
    return out


@register_source("firms")
def firms(opts: dict, ctx) -> PointTable:
    """Fetch each satellite's open FIRMS CSV for the period (cached for max_age_minutes, default 60) and return the detections from that many hours before the latest one, as points with fire radiative power, plus fires_from and fires_to, the period's start and the latest acquisition time. The files reach further back than they're named for (a "24h" file held 43 hours), so the cut keeps the title true. Tested by test_v3_sources.py::test_firms_reads_fires with stubbed HTTP; the live files are exercised by building fires_burning_now."""
    check_keys("source firms", opts, {"satellites", "period", "min_confidence", "max_age_minutes"})
    sats = list(opts.get("satellites", list(SATELLITES)))
    period = opts.get("period", "24h")
    floor = opts.get("min_confidence", "nominal")
    if not sats or any(s not in SATELLITES for s in sats):
        raise RecipeError(f"source firms: satellites are some of {', '.join(SATELLITES)}")
    if period not in PERIODS:
        raise RecipeError(f"source firms: period is one of {', '.join(PERIODS)}")
    if floor not in ("low", "nominal", "high"):
        raise RecipeError("source firms: min_confidence is low, nominal or high")
    cols: dict[str, list] = {}
    raws = []
    for sat in sats:
        url = firms_url(sat, period)
        path = cached_call("firms", {"url": url}, lambda u=url: http_request(u, timeout=180), suffix=".csv",
                           max_age_days=float(opts.get("max_age_minutes", 60)) / 1440.0,
                           refresh=bool(getattr(ctx, "refresh", False)))
        raw = path.read_bytes()
        raws.append(raw)
        part = parse_firms(raw.decode("utf-8-sig"), floor)
        LOG.info("firms: %s %s, %d detections", sat, period, len(part["lat"]))
        for k, v in part.items():
            cols.setdefault(k, []).extend(v)
    if not cols.get("lat"):
        raise RecipeError(f"FIRMS has no detections for {', '.join(sats)} over {period}: a satellite may be down")
    times = cols.pop("time")
    latest = max(times)
    start = latest - timedelta(hours=PERIODS[period])
    keep = np.array([t > start for t in times])
    table = {k: np.array(v, dtype=float if k in ("lat", "lon", "frp") else object)[keep] for k, v in cols.items()}
    table["time"] = np.array([t.isoformat() for t in times], dtype=object)[keep]
    LOG.info("firms: %d detections in the %s to %s UTC", int(keep.sum()), period, latest.strftime("%d %b %H:%M"))
    return PointTable(table, ATTRIBUTION, "NASA open data; acknowledge NASA FIRMS", digest_bytes(*raws),
                      times={"fires_from": start, "fires_to": latest})
