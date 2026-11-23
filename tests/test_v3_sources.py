"""Tests for the v0.3.0 sources: aurora grid, regions, country tables and the GHSL GeoPackage reader."""
from __future__ import annotations

import json
import math
import sqlite3
import struct
from types import SimpleNamespace

import numpy as np
import pytest

from conftest import write_recipe
from sosforge.fetch import Asset
from sosforge.recipe import RecipeError, load_recipe
from sosforge.sources import aurora, ghsl, load_source, regions

RECIPE = '[dataset]\nname="s"\n[[layers]]\ntype="basemap"\ncolor="#000000"\n'


def ctx_for(tmp_path, files=None):
    """Return a minimal context with a recipe rooted in a temp folder. Helper; not itself tested."""
    return SimpleNamespace(recipe=load_recipe(write_recipe(tmp_path / "r", RECIPE, files)), refresh=False)


def ovation_doc(hot=(10, 65, 80)) -> dict:
    """Return an OVATION-shaped document with one hot cell. Helper; not itself tested."""
    coords = [[lon, lat, hot[2] if (lon, lat) == hot[:2] else 0] for lon in range(360) for lat in range(-90, 91)]
    return {"Observation Time": "2026-11-23T00:37:00Z", "Forecast Time": "2026-11-23T01:56:00Z",
            "Data Format": "[Longitude, Latitude, Aurora]", "coordinates": coords, "type": "MultiPoint"}


def test_swpc_aurora_parses_grid(tmp_path, monkeypatch):
    """Checks the OVATION lattice becomes a north-indexed 1-degree grid with its times, and the source caches the feed. Covers parse_ovation and swpc_aurora with stubbed HTTP; the live feed is exercised by real builds."""
    g = aurora.parse_ovation(ovation_doc())
    assert g.values.shape == (181, 360) and (g.lon0, g.dlon, g.lat0, g.dlat) == (0.0, 1.0, -90.0, 1.0)
    assert g.values[65 + 90, 10] == 80 and g.times["forecast_time"].hour == 1
    calls = []
    monkeypatch.setattr(aurora, "http_request", lambda url, **kw: (calls.append(url), json.dumps(ovation_doc()).encode())[1])
    src = load_source({"type": "swpc_aurora"}, ctx_for(tmp_path))
    load_source({"type": "swpc_aurora"}, ctx_for(tmp_path))
    assert src.values.max() == 80 and src.fingerprint and len(calls) == 1 and "NOAA" in src.attribution
    with pytest.raises(RecipeError, match="unexpected OVATION"):
        aurora.parse_ovation({"coordinates": []})


GEO = {"type": "FeatureCollection", "features": [
    {"type": "Feature", "properties": {"ISO_A3_EH": "AAA", "NAME": "Alpha", "ADM0_A3": "AAA"},
     "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]], [[2, 2], [3, 2], [3, 3], [2, 2]]]}},
    {"type": "Feature", "properties": {"ISO_A3_EH": "-99", "NAME": "Beta", "ADM0_A3": "BBB"},
     "geometry": {"type": "MultiPolygon", "coordinates": [[[[20, 0], [30, 0], [30, 5], [20, 0]]], [[[40, 0], [45, 0], [45, 5], [40, 0]]]]}},
    {"type": "Feature", "properties": {"ISO_A3_EH": "CCC", "NAME": "Gamma"}, "geometry": {"type": "Point", "coordinates": [1, 1]}}]}


def test_natural_earth_and_geojson_regions(tmp_path, monkeypatch):
    """Checks outer rings are kept (holes dropped), -99 codes fall back to ADM0_A3, non-polygons are skipped, and both region sources load. Covers rings_of, regions_from_geojson, natural_earth and geojson_regions; the real Natural Earth download is exercised by builds."""
    codes, names, rings = regions.regions_from_geojson(GEO, "ISO_A3_EH", "NAME", fallback_code="ADM0_A3")
    assert codes == ["AAA", "BBB"] and names == ["Alpha", "Beta"] and len(rings[0]) == 1 and len(rings[1]) == 2
    src = tmp_path / "ne.geojson"
    src.write_text(json.dumps(GEO))
    data = src.read_bytes()
    import hashlib
    monkeypatch.setitem(regions.NATURAL_EARTH, "50m", Asset("ne", src.as_uri(), "ne_test.geojson", len(data),
                                                            hashlib.sha256(data).hexdigest()))
    ne = load_source({"type": "natural_earth"}, ctx_for(tmp_path))
    assert ne.codes == ["AAA", "BBB"] and "Natural Earth" in ne.attribution
    gj = load_source({"type": "geojson_regions", "path": "g.geojson", "code": "ISO_A3_EH", "name": "NAME"},
                     ctx_for(tmp_path / "x", {"g.geojson": json.dumps(GEO)}))
    assert gj.codes == ["AAA"] and gj.names == ["Alpha"]
    with pytest.raises(RecipeError, match="scale"):
        load_source({"type": "natural_earth", "scale": "10m"}, ctx_for(tmp_path))


def test_owid_and_table_sources(tmp_path, monkeypatch):
    """Checks the OWID chart's value column is found past annotations, its citation is credited, and a plain CSV table loads with renamed columns. Covers owid, table and table_from_csv with stubbed HTTP; the live OWID API is exercised by builds."""
    csv_text = "entity,code,year,gdp_per_capita,gdp_per_capita__annotations\nAlpha,AAA,1900,1000,\nWorld,,1900,900,\nAlpha,AAA,2000,8000,x\n"
    meta = {"columns": {"gdp": {"citationShort": "Bolt and van Zanden – Maddison Project Database 2023", "unit": "international-$"}}}
    monkeypatch.setattr(regions, "http_request", lambda url, **kw: (json.dumps(meta) if "metadata" in url else csv_text).encode())
    t = load_source({"type": "owid", "slug": "gdp-per-capita-maddison-project-database"}, ctx_for(tmp_path))
    assert t.columns["code"].tolist() == ["AAA", "AAA"] and t.columns["value"].tolist() == [1000.0, 8000.0]
    assert "Maddison" in t.attribution and "CC BY 4.0" in t.attribution and t.columns["unit"][0] == "international-$"
    with pytest.raises(RecipeError, match="slug"):
        load_source({"type": "owid"}, ctx_for(tmp_path))
    ctx = ctx_for(tmp_path / "t", {"d.csv": "iso,yr,v\nAAA,2000,5\nBBB,2001,7\n", "bad.csv": "iso,yr,v\nAAA,2000,x\n"})
    tb = load_source({"type": "table", "path": "d.csv", "code": "iso", "year": "yr", "value": "v"}, ctx)
    assert tb.column("code").tolist() == ["AAA", "BBB"] and tb.column("year").tolist() == [2000.0, 2001.0]
    with pytest.raises(RecipeError, match="not numeric"):
        load_source({"type": "table", "path": "bad.csv", "code": "iso", "year": "yr", "value": "v"}, ctx)
    with pytest.raises(RecipeError, match="available"):
        tb.column("nope")


def forward_mollweide(lat: float, lon: float, r: float = ghsl.R_MOLLWEIDE) -> tuple[float, float]:
    """Project degrees to World Mollweide metres by Newton iteration, to test the inverse. Helper; not itself tested."""
    phi, theta = math.radians(lat), math.radians(lat)
    for _ in range(50):
        theta -= (2 * theta + math.sin(2 * theta) - math.pi * math.sin(phi)) / (2 + 2 * math.cos(2 * theta) + 1e-15)
    return 2 * math.sqrt(2) * r * math.radians(lon) * math.cos(theta) / math.pi, math.sqrt(2) * r * math.sin(theta)


def gpkg_blob(x: float, y: float, envelope: bool = False) -> bytes:
    """Encode a little-endian GeoPackage point blob, optionally with an XY envelope. Helper; not itself tested."""
    flags = 0b00000001 | ((1 << 1) if envelope else 0)
    head = b"GP" + bytes([0, flags]) + struct.pack("<i", 54009)
    env = struct.pack("<4d", x, x, y, y) if envelope else b""
    return head + env + struct.pack("<BIdd", 1, 1, x, y)


def test_mollweide_and_gpkg_points(tmp_path):
    """Checks the inverse Mollweide against forward projection, point blobs with and without envelopes, and reading names, places and populations from a GeoPackage. Covers inverse_mollweide, gpkg_point and read_points; the real GHSL download is exercised by builds."""
    for lat, lon in ((0, 0), (51.5, -0.13), (-33.9, 151.2), (35.7, 139.7)):
        la, lo = ghsl.inverse_mollweide(*map(np.array, forward_mollweide(lat, lon)))
        assert float(la) == pytest.approx(lat, abs=1e-6) and float(lo) == pytest.approx(lon, abs=1e-6)
    assert ghsl.gpkg_point(gpkg_blob(1.5, -2.5, envelope=True)) == (1.5, -2.5)
    with pytest.raises(RecipeError):
        ghsl.gpkg_point(b"XX" + bytes(30))
    db = tmp_path / "u.gpkg"
    con = sqlite3.connect(db)
    con.execute(f'create table "{ghsl.TABLE}" (GC_UCN_MAI_2025 text, GC_CNT_GAD_2025 text, geom blob, '
                'MT_POP_TOT_1975 integer, MT_POP_TOT_2030 integer)')
    for name, (lat, lon), p in (("Tokyo", (35.7, 139.7), (2.6e7, 3.7e7)), ("Lagos", (6.5, 3.4), (1.9e6, 2.1e7))):
        con.execute(f'insert into "{ghsl.TABLE}" values (?,?,?,?,?)', (name, "X", gpkg_blob(*forward_mollweide(lat, lon)), *p))
    con.commit()
    con.close()
    cols = ghsl.read_points(db)
    assert cols["name"].tolist() == ["Tokyo", "Lagos"] and np.allclose(cols["lat"], [35.7, 6.5], atol=1e-6)
    assert cols["MT_POP_TOT_2030"].tolist() == [3.7e7, 2.1e7]
