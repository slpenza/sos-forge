"""Regions and the values that shade them: Natural Earth countries (public domain), any GeoJSON polygons, Our World
in Data country-by-year tables (CC BY 4.0), and plain CSV tables of code, year and value."""
from __future__ import annotations

import csv
import io
import json
import logging

import numpy as np

from ..fetch import Asset, cached_call, ensure_asset, http_request
from ..recipe import RecipeError, check_keys
from ..registry import register_source
from .base import DataTable, RegionSet, digest_bytes, numeric_or_text

LOG = logging.getLogger("sosforge.regions")
NE_BASE = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/v5.1.2/geojson/"
NATURAL_EARTH = {
    "50m": Asset("ne_50m_countries", NE_BASE + "ne_50m_admin_0_countries.geojson", "ne_50m_admin_0_countries.geojson",
                 3083490, "3e458fc036ad0a66411f2c1e6cac49c5d7bfb81cb1123bc513b22511a2b7fdeb"),
    "110m": Asset("ne_110m_countries", NE_BASE + "ne_110m_admin_0_countries.geojson",
                  "ne_110m_admin_0_countries.geojson", 838726, None),
}
OWID_URL = "https://ourworldindata.org/grapher/{slug}.csv?v=1&csvType=full&useColumnShortNames=true"
OWID_META = "https://ourworldindata.org/grapher/{slug}.metadata.json?v=1&csvType=full&useColumnShortNames=true"


def rings_of(geometry: dict) -> list[np.ndarray]:
    """Return the outer rings of a Polygon or MultiPolygon as (N, 2) lon/lat arrays, ignoring holes. Tested by test_v3_sources.py::test_natural_earth_and_geojson_regions."""
    kind, coords = (geometry or {}).get("type"), (geometry or {}).get("coordinates") or []
    polys = [coords] if kind == "Polygon" else coords if kind == "MultiPolygon" else []
    return [np.asarray(p[0], dtype=float)[:, :2] for p in polys if p and len(p[0]) >= 3]


def regions_from_geojson(doc: dict, code_key: str, name_key: str, fallback_code: str | None = None) -> tuple:
    """Read codes, names and outer rings from GeoJSON features, using a fallback property when the code is missing or -99. Tested by test_v3_sources.py::test_natural_earth_and_geojson_regions."""
    codes, names, rings = [], [], []
    for f in doc.get("features", []):
        props = f.get("properties") or {}
        code = str(props.get(code_key) or "")
        if (not code or code == "-99") and fallback_code:
            code = str(props.get(fallback_code) or "")
        r = rings_of(f.get("geometry"))
        if code and code != "-99" and r:
            codes.append(code)
            names.append(str(props.get(name_key) or code))
            rings.append(r)
    return codes, names, rings


@register_source("natural_earth")
def natural_earth(opts: dict, ctx) -> RegionSet:
    """Download (once, size- and hash-checked) Natural Earth countries and return them keyed by ISO 3166 alpha-3. Tested by test_v3_sources.py::test_natural_earth_and_geojson_regions with a local copy."""
    check_keys("source natural_earth", opts, {"scale"})
    scale = opts.get("scale", "50m")
    if scale not in NATURAL_EARTH:
        raise RecipeError(f"natural_earth scale must be one of {', '.join(NATURAL_EARTH)}")
    path = ensure_asset(NATURAL_EARTH[scale])
    raw = path.read_bytes()
    codes, names, rings = regions_from_geojson(json.loads(raw), "ISO_A3_EH", "NAME", fallback_code="ADM0_A3")
    return RegionSet(codes, names, rings, "Natural Earth (public domain)", "Public domain", digest_bytes(raw))


@register_source("geojson_regions")
def geojson_regions(opts: dict, ctx) -> RegionSet:
    """Load polygons from a recipe-relative GeoJSON file, keyed by a property, for shading your own regions. Tested by test_v3_sources.py::test_natural_earth_and_geojson_regions."""
    check_keys("source geojson_regions", opts, {"path", "code", "name", "attribution", "license"})
    path = ctx.recipe.resolve(opts["path"])
    raw = path.read_bytes()
    codes, names, rings = regions_from_geojson(json.loads(raw), opts.get("code", "code"), opts.get("name", "name"))
    return RegionSet(codes, names, rings, opts.get("attribution", ""), opts.get("license", ""), digest_bytes(raw))


def table_from_csv(text: str, code: str, year: str, value: str) -> dict[str, np.ndarray]:
    """Read code, year and value columns from CSV text into arrays, dropping rows without a code or a number. Tested by test_v3_sources.py::test_owid_and_table_sources."""
    rows = list(csv.DictReader(io.StringIO(text)))
    if rows and any(k not in rows[0] for k in (code, year, value)):
        raise RecipeError(f"table needs columns {code!r}, {year!r} and {value!r} (found {', '.join(rows[0])})")
    keep = [r for r in rows if r.get(code) and r.get(value) not in (None, "")]
    vals = numeric_or_text([r[value] for r in keep])
    if vals.dtype == object:
        raise RecipeError(f"column {value!r} is not numeric")
    return {"code": np.array([r[code] for r in keep], dtype=object),
            "year": np.array([float(r[year]) for r in keep]), "value": vals}


@register_source("owid")
def owid(opts: dict, ctx) -> DataTable:
    """Fetch an Our World in Data chart's full CSV and citation by slug (cached 30 days) as code, year and value. Tested by test_v3_sources.py::test_owid_and_table_sources with stubbed HTTP."""
    check_keys("source owid", opts, {"slug", "column", "max_age_days"})
    if "slug" not in opts:
        raise RecipeError('owid source needs slug = "<chart name from ourworldindata.org/grapher/...>"')
    slug, age = opts["slug"], float(opts.get("max_age_days", 30))
    data = cached_call("owid", {"slug": slug, "kind": "csv"}, lambda: http_request(OWID_URL.format(slug=slug)),
                       suffix=".csv", max_age_days=age, refresh=ctx.refresh).read_text(encoding="utf-8")
    meta_raw = cached_call("owid", {"slug": slug, "kind": "meta"}, lambda: http_request(OWID_META.format(slug=slug)),
                           suffix=".json", max_age_days=age, refresh=ctx.refresh).read_text(encoding="utf-8")
    header = next(csv.reader(io.StringIO(data)))
    value = opts.get("column") or next((c for c in header if c not in ("entity", "code", "year")
                                        and not c.endswith("__annotations")), None)
    if not value:
        raise RecipeError(f"no value column in OWID chart {slug!r} (columns: {', '.join(header)})")
    meta = json.loads(meta_raw)
    col = next(iter(meta.get("columns", {}).values()), {})
    cite = col.get("citationShort") or meta.get("chart", {}).get("citation") or "Our World in Data"
    cols = table_from_csv(data, "code", "year", value)
    unit = col.get("unit") or ""
    return DataTable({**cols, "unit": np.array([unit], dtype=object)},
                     f"{cite}; Our World in Data (CC BY 4.0)", "CC-BY-4.0", digest_bytes(data.encode()))


@register_source("table")
def table(opts: dict, ctx) -> DataTable:
    """Load a recipe-relative CSV of code, year and value columns (renamable) for shading regions. Tested by test_v3_sources.py::test_owid_and_table_sources."""
    check_keys("source table", opts, {"path", "code", "year", "value", "attribution", "license"})
    path = ctx.recipe.resolve(opts["path"])
    raw = path.read_bytes()
    cols = table_from_csv(raw.decode("utf-8-sig"), opts.get("code", "code"), opts.get("year", "year"),
                          opts.get("value", "value"))
    return DataTable(cols, opts.get("attribution", ""), opts.get("license", ""), digest_bytes(raw))

