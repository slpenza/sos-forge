"""Pinball Map (pinballmap.com) locations via its API.

Every API call needs a token (request one at https://pinballmap.com/api_token; approval is manual).  The token
is read from $PINBALLMAP_API_TOKEN and never written to disk or logs.  Data is CC BY-SA 4.0: datasets built
from it must credit Pinball Map, link to pinballmap.com, and be shared under the same licence.
Field names are normalised defensively because the bulk endpoint's schema was not verifiable without a token.
"""
from __future__ import annotations

import json
import logging
import os

import numpy as np

from ..fetch import cached_call, http_request
from ..recipe import RecipeError, check_keys
from ..registry import register_source
from .base import PointTable, digest_bytes

LOG = logging.getLogger("sosforge.pinballmap")
DEFAULT_URL = "https://pinballmap.com/api/v1/locations.json"
ATTRIBUTION = "Pinball Map (https://pinballmap.com), CC BY-SA 4.0"
LAT_KEYS, LON_KEYS = ("lat", "latitude"), ("lon", "lng", "longitude")
COUNT_KEYS = ("num_machines", "machine_count", "location_machine_xrefs_count")
LIST_KEYS = ("machine_names", "location_machine_xrefs", "machines")


def _first(d: dict, keys: tuple) -> object:
    """Return the first present, non-empty value among candidate keys. Tested indirectly by test_sources.py::test_pinballmap_normalises_fields."""
    for k in keys:
        if d.get(k) not in (None, ""):
            return d[k]
    return None


def normalise(doc) -> dict[str, np.ndarray]:
    """Turn a Pinball Map locations payload into lat, lon, num_machines, created_at, name and id columns. Tested by test_sources.py::test_pinballmap_normalises_fields."""
    locs = doc.get("locations", doc) if isinstance(doc, dict) else doc
    if not isinstance(locs, list):
        raise RuntimeError(f"unexpected Pinball Map payload (keys: {', '.join(doc) if isinstance(doc, dict) else type(doc).__name__})")
    lat, lon, n, created, name, ident = [], [], [], [], [], []
    for loc in locs:
        la, lo = _first(loc, LAT_KEYS), _first(loc, LON_KEYS)
        if la is None or lo is None:
            continue
        count = _first(loc, COUNT_KEYS)
        if count is None:
            lst = _first(loc, LIST_KEYS)
            count = len(lst) if isinstance(lst, list) else 1
        lat.append(float(la))
        lon.append(float(lo))
        n.append(float(count))
        created.append(loc.get("created_at") or "")
        name.append(loc.get("name") or "")
        ident.append(loc.get("id"))
    if locs and not lat:
        raise RuntimeError(f"no coordinates found; first location keys: {', '.join(sorted(locs[0]))}")
    return {"lat": np.array(lat), "lon": np.array(lon), "num_machines": np.array(n),
            "created_at": np.array(created, dtype=object), "name": np.array(name, dtype=object),
            "id": np.array(ident, dtype=object)}


@register_source("pinballmap")
def pinballmap_source(opts: dict, ctx) -> PointTable:
    """Fetch (or reuse a cached copy of) all Pinball Map locations with the API token and return a PointTable. Tested by test_sources.py::test_pinballmap_token_required and test_pinballmap_normalises_fields with stubbed HTTP; the live API is untested pending a token."""
    check_keys("source pinballmap", opts, {"url", "params", "token_env", "max_age_days"})
    url, params = opts.get("url", DEFAULT_URL), dict(opts.get("params", {}))
    token_env = opts.get("token_env", "PINBALLMAP_API_TOKEN")

    def fetch() -> bytes:
        """Call the API with the token header, failing early with signup instructions when the token is missing. Tested by test_sources.py::test_pinballmap_token_required."""
        token = os.environ.get(token_env, "").strip()
        if not token:
            raise RecipeError(f"Pinball Map needs an API token: request one at https://pinballmap.com/api_token, "
                              f"then `export {token_env}=...` (it is never stored in the repo)")
        LOG.info("pinballmap: GET %s", url)
        return http_request(url, params=params or None, headers={"X-Api-Token": token, "Accept": "application/json"})

    path = cached_call("pinballmap", {"url": url, "params": params}, fetch, suffix=".json",
                       max_age_days=float(opts.get("max_age_days", 7)), refresh=ctx.refresh)
    raw = path.read_bytes()
    cols = normalise(json.loads(raw))
    LOG.info("pinballmap: %d locations, %d machines", len(cols["lat"]), int(cols["num_machines"].sum()))
    return PointTable(cols, ATTRIBUTION, "CC-BY-SA-4.0", digest_bytes(raw))
