"""OpenStreetMap features via the Overpass API, one cached query per category.

The public overpass-api.de instance is meant for light use: queries are cached (default 30 days), sent one at a
time with a pause, identified by User-Agent, retried with exponential backoff, and requested as compact CSV.
For heavy use point `endpoint` (or $SOSFORGE_OVERPASS_URL) at your own Overpass instance.
OSM data is ODbL: rendered images are Produced Works and must credit "© OpenStreetMap contributors".
"""
from __future__ import annotations

import logging
import os
import time

import numpy as np

from ..fetch import cached_call, http_request
from ..recipe import RecipeError, check_keys
from ..registry import register_source
from .base import PointTable, digest_bytes

LOG = logging.getLogger("sosforge.overpass")
DEFAULT_ENDPOINT = "https://overpass-api.de/api/interpreter"
HEADER = "@lat\t@lon\t@id\t@type"
ATTRIBUTION = "© OpenStreetMap contributors (ODbL), via the Overpass API"


def build_query(statement: str, timeout_s: int) -> str:
    """Wrap an Overpass QL statement so it returns tab-separated lat, lon, id and type with way/relation centres. Tested by test_sources.py::test_overpass_query_parse_and_cache."""
    stmt = statement.strip().rstrip(";")
    return (f'[out:csv(::lat,::lon,::id,::type;true;"\\t")][timeout:{timeout_s}];\n'
            f"(\n  {stmt};\n);\nout center;\n")


def parse_csv(body: bytes, statement: str) -> np.ndarray:
    """Parse Overpass CSV output into an (N, 2) lat/lon array, rejecting HTML/XML error pages and partial results. Tested by test_sources.py::test_overpass_query_parse_and_cache."""
    text = body.decode("utf-8", "replace")
    lines = text.splitlines()
    if not lines or lines[0].strip() != HEADER:
        snippet = " ".join(text.split())[:200]
        raise RuntimeError(f"Overpass did not return CSV for {statement!r}: {snippet}")
    pts = []
    for ln in lines[1:]:
        parts = ln.split("\t")
        if len(parts) >= 2 and parts[0] and parts[1]:
            try:
                pts.append((float(parts[0]), float(parts[1])))
            except ValueError as exc:
                raise RuntimeError(f"Overpass returned a malformed row for {statement!r}: {ln[:80]}") from exc
    return np.array(pts, dtype=float).reshape(-1, 2)


@register_source("overpass")
def overpass_source(opts: dict, ctx) -> PointTable:
    """Run each {category, query} through Overpass (cached) and return points with a category column. Tested by test_sources.py::test_overpass_query_parse_and_cache with a stubbed HTTP call."""
    check_keys("source overpass", opts, {"queries", "endpoint", "max_age_days", "timeout_s", "pause_s"})
    queries = opts.get("queries") or []
    if not queries:
        raise RecipeError("overpass source needs queries = [{ category = \"...\", query = 'nwr[\"sport\"=\"cricket\"]' }, ...]")
    endpoint = opts.get("endpoint") or os.environ.get("SOSFORGE_OVERPASS_URL") or DEFAULT_ENDPOINT
    timeout_s, pause_s = int(opts.get("timeout_s", 300)), float(opts.get("pause_s", 3.0))
    lat, lon, cat, digests, fetched = [], [], [], [], 0
    for q in queries:
        check_keys("overpass query", q, {"category", "query"})
        ql = build_query(q["query"], timeout_s)

        def fetch(ql=ql):
            """Fetch one query from Overpass, pausing first between live requests. Tested indirectly by test_sources.py::test_overpass_query_parse_and_cache."""
            nonlocal fetched
            if fetched:
                time.sleep(pause_s)
            fetched += 1
            LOG.info("overpass: querying %s (%s)", q["category"], endpoint)
            body = http_request(endpoint, data=f"data={_quote(ql)}".encode(), timeout=timeout_s + 60,
                                headers={"Content-Type": "application/x-www-form-urlencoded"})
            parse_csv(body, q["query"])
            return body

        path = cached_call("overpass", {"ql": ql}, fetch, suffix=".tsv", max_age_days=float(opts.get("max_age_days", 30)),
                           refresh=ctx.refresh)
        body = path.read_bytes()
        pts = parse_csv(body, q["query"])
        LOG.info("overpass: %-20s %7d features", q["category"], len(pts))
        lat.append(pts[:, 0])
        lon.append(pts[:, 1])
        cat.extend([q["category"]] * len(pts))
        digests.append(body)
    cols = {"lat": np.concatenate(lat), "lon": np.concatenate(lon), "category": np.array(cat, dtype=object)}
    return PointTable(cols, ATTRIBUTION, "ODbL-1.0", digest_bytes(*digests))


def _quote(text: str) -> str:
    """URL-encode an Overpass QL string for a form POST body. Tested indirectly by test_sources.py::test_overpass_query_parse_and_cache."""
    from urllib.parse import quote
    return quote(text, safe="")

