"""Local point files: CSV with lat/lon columns, and GeoJSON Point/MultiPoint features."""
from __future__ import annotations

import csv
import io
import json

import numpy as np

from ..recipe import check_keys
from ..registry import register_source
from .base import PointTable, digest_bytes, numeric_or_text


@register_source("csv")
def csv_source(opts: dict, ctx) -> PointTable:
    """Load a CSV with latitude/longitude columns (renamable) into a PointTable, keeping every other column. Tested by test_sources.py::test_csv_and_geojson_points."""
    check_keys("source csv", opts, {"path", "lat", "lon", "delimiter", "attribution", "license"})
    path = ctx.recipe.resolve(opts["path"])
    raw = path.read_bytes()
    rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig")), delimiter=opts.get("delimiter", ",")))
    lat_key, lon_key = opts.get("lat", "lat"), opts.get("lon", "lon")
    if rows and (lat_key not in rows[0] or lon_key not in rows[0]):
        raise KeyError(f"{path.name}: needs '{lat_key}' and '{lon_key}' columns (found {', '.join(rows[0])})")
    cols = {k: numeric_or_text([r.get(k) for r in rows]) for k in (rows[0] if rows else [lat_key, lon_key])}
    cols["lat"], cols["lon"] = cols.pop(lat_key).astype(float), cols.pop(lon_key).astype(float)
    return PointTable(cols, opts.get("attribution", ""), opts.get("license", ""), digest_bytes(raw))


@register_source("geojson")
def geojson_source(opts: dict, ctx) -> PointTable:
    """Load Point and MultiPoint features from a GeoJSON file, flattening properties into columns. Tested by test_sources.py::test_csv_and_geojson_points."""
    check_keys("source geojson", opts, {"path", "attribution", "license"})
    path = ctx.recipe.resolve(opts["path"])
    raw = path.read_bytes()
    doc = json.loads(raw)
    feats = doc.get("features", [doc] if doc.get("type") == "Feature" else [])
    pts, props = [], []
    for f in feats:
        g = f.get("geometry") or {}
        coords = [g["coordinates"]] if g.get("type") == "Point" else g.get("coordinates", []) if g.get("type") == "MultiPoint" else []
        for c in coords:
            pts.append((c[1], c[0]))
            props.append(f.get("properties") or {})
    keys = sorted({k for p in props for k in p})
    cols = {k: numeric_or_text([p.get(k) for p in props]) for k in keys if k not in ("lat", "lon")}
    cols["lat"] = np.array([p[0] for p in pts], dtype=float)
    cols["lon"] = np.array([p[1] for p in pts], dtype=float)
    return PointTable(cols, opts.get("attribution", ""), opts.get("license", ""), digest_bytes(raw))
