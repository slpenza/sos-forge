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


def test_ghsl_pop_sums_people(tmp_path):
    """Checks GHS-POP's grid is summed into coarse cells by each source cell's centre, read tile by tile from a GeoTIFF with GHSL's georeferencing: people land in the right cell, the source's extra columns beyond +/-180 and its no-data, negative and NaN cells count as nobody, the cell areas add up to the sphere, density is people per square kilometre at cell centres, and the total is reported; and a bad cells_per_degree is refused before any download. Covers population_counts, runs, cell_areas_km2, population_field and ghsl_pop's option check against a synthetic GeoTIFF; the real download and grid are checked by building where_people_live."""
    import tifffile
    # Half-degree cells, one extra column each side of +/-180 as the real grid has, in 16-pixel tiles.
    grid = np.zeros((360, 722), np.float64)
    grid[89, 381] = 1000.0        # centre 45.25N, 10.25E: cell (44, 190) at 1 cell per degree
    grid[88, 381] = 500.0         # centre 45.75N, 10.25E: the same 1-degree cell
    grid[90, 381] = 50.0          # centre 44.75N, 10.25E: the cell south of it, (45, 190)
    grid[0, 0] = 999.0            # centred at 180.25W: beyond +/-180, dropped
    grid[10, 721] = 777.0         # centred at 180.25E: dropped
    grid[200, 100] = -200.0       # no data
    grid[201, 100] = np.nan
    grid[300, 1] = 7.0            # centre 179.75W, 60.25S: the first kept column
    tif = tmp_path / "pop.tif"
    tifffile.imwrite(tif, grid, tile=(16, 16), compression="zlib",
                     extratags=[(33550, "d", 3, (0.5, 0.5, 0.0), False), (33922, "d", 6, (0, 0, 0, -180.5, 90.0, 0), False)])
    counts = ghsl.population_counts(tif, 1)
    assert counts.shape == (180, 360) and counts.sum() == pytest.approx(1557.0)
    assert (counts[44, 190], counts[45, 190], counts[150, 0]) == (1500.0, 50.0, 7.0)
    areas = ghsl.cell_areas_km2(1)
    assert areas.sum() * 360 == pytest.approx(4 * math.pi * ghsl.EARTH_RADIUS_KM ** 2, rel=1e-9)
    f = ghsl.population_field(counts, 1, "abc")
    assert (f.lon0, f.dlon, f.lat0, f.dlat) == (-179.5, 1.0, 89.5, -1.0)
    assert f.values[44, 190] == pytest.approx(1500.0 / areas[44], rel=1e-6)
    assert f.stats["people"] == pytest.approx(1557.0) and f.license == "CC-BY-4.0" and f.fingerprint == "abc:1"
    with pytest.raises(RecipeError, match="cells_per_degree must be 1 to 120"):
        ghsl.ghsl_pop({"cells_per_degree": 0}, None)


FIRMS_HEADER = "latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,confidence,version,bright_ti5,frp,daynight\n"


def firms_csv(rows) -> bytes:
    """Return a FIRMS VIIRS CSV from (lat, lon, date, hhmm, confidence, frp) rows. Helper for test_firms_reads_fires; not itself tested."""
    return (FIRMS_HEADER + "".join(f"{la},{lo},330.0,0.5,0.5,{d},{t},N20,{c},2.0NRT,295.0,{f},D\n"
                                   for la, lo, d, t, c, f in rows)).encode()


def test_firms_reads_fires(tmp_path, monkeypatch):
    """Checks the FIRMS source reads both satellites' open CSVs once (then from the cache), keeps detections at or above nominal confidence from the 24 hours before the latest one, reports that window as fires_from and fires_to, keeps fire radiative power, and refuses unknown satellites, periods and confidences, a row it can't read, and files with no detections. Covers firms_url, parse_firms and firms with stubbed HTTP; the live files are exercised by building fires_burning_now."""
    from datetime import datetime, timezone
    from sosforge.sources import firms
    files = {
        "J1_VIIRS_C2": firms_csv([(10.5, 20.5, "2026-11-25", "1923", "nominal", 12.5),
                                  (-15.0, 30.0, "2026-11-25", "0105", "high", 80.0),
                                  (40.0, -100.0, "2026-11-24", "0000", "nominal", 5.0),    # 43 hours before: cut
                                  (0.0, 0.0, "2026-11-25", "1200", "low", 1.0)]),          # low confidence: dropped
        "J2_VIIRS_C2": firms_csv([(5.0, 5.0, "2026-11-24", "1924", "n", 7.0)]),             # just inside 24 hours
    }
    calls = []

    def fake(url, **kw):
        calls.append(url)
        return next(v for k, v in files.items() if k in url)
    monkeypatch.setattr(firms, "http_request", fake)
    t = load_source({"type": "firms"}, ctx_for(tmp_path))
    load_source({"type": "firms"}, ctx_for(tmp_path))
    assert len(calls) == 2 and calls[0].endswith("/noaa-20-viirs-c2/csv/J1_VIIRS_C2_Global_24h.csv")
    assert sorted(t.column("frp").tolist()) == [7.0, 12.5, 80.0] and "FIRMS" in t.attribution
    to = datetime(2026, 11, 25, 19, 23, tzinfo=timezone.utc)
    assert t.times == {"fires_from": datetime(2026, 11, 24, 19, 23, tzinfo=timezone.utc), "fires_to": to}
    low = load_source({"type": "firms", "min_confidence": "low", "max_age_minutes": 0}, ctx_for(tmp_path))
    assert len(low) == 4
    for bad, match in (({"satellites": ["terra"]}, "satellites"), ({"period": "1h"}, "period"),
                       ({"min_confidence": "medium"}, "min_confidence")):
        with pytest.raises(RecipeError, match=match):
            load_source({"type": "firms", **bad}, ctx_for(tmp_path))
    with pytest.raises(RecipeError, match="unexpected FIRMS row"):
        firms.parse_firms(FIRMS_HEADER + "x,1,2,3,4,2026-11-25,0100,N20,nominal,2,3,4,D\n")
    files.update(J1_VIIRS_C2=FIRMS_HEADER.encode(), J2_VIIRS_C2=FIRMS_HEADER.encode())
    with pytest.raises(RecipeError, match="no detections"):
        load_source({"type": "firms", "max_age_minutes": 0}, ctx_for(tmp_path))


def usgs_doc(quakes, generated_ms=1795645454000) -> dict:
    """Return a USGS summary feed from (lon, lat, depth, mag, type, time_ms) tuples. Helper for test_usgs_reads_quakes; not itself tested."""
    return {"type": "FeatureCollection", "metadata": {"generated": generated_ms, "title": "test", "count": len(quakes)},
            "features": [{"type": "Feature", "properties": {"mag": m, "place": f"near {i}", "time": t, "type": k},
                          "geometry": {"type": "Point", "coordinates": [lo, la, d]}}
                         for i, (lo, la, d, m, k, t) in enumerate(quakes)]}


def test_usgs_reads_quakes(tmp_path, monkeypatch):
    """Checks the USGS source reads a summary feed once (then from the cache) as points with magnitude, depth and a strength about three times brighter per magnitude, keeps only earthquakes with a magnitude, reports the feed's 7-day window and its strongest quake, and refuses an unknown feed, a feed with no earthquakes and a document that isn't a feed. Covers parse_feed and usgs_quakes with stubbed HTTP; the live feed is exercised by building earthquakes_this_week."""
    from datetime import datetime, timezone
    from sosforge.sources import usgs
    doc = usgs_doc([(178.6, -35.9, 10.0, 4.5, "earthquake", 1795642353545), (142.0, 38.0, 30.0, 6.5, "earthquake", 1795600000000),
                    (-118.0, 34.0, 1.0, 4.6, "quarry blast", 1795600000000), (10.0, 45.0, 5.0, None, "earthquake", 1795600000000)])
    calls = []
    monkeypatch.setattr(usgs, "http_request", lambda url, **kw: (calls.append(url), json.dumps(doc).encode())[1])
    t = load_source({"type": "usgs_quakes"}, ctx_for(tmp_path))
    load_source({"type": "usgs_quakes"}, ctx_for(tmp_path))
    assert len(calls) == 1 and calls[0].endswith("/summary/4.5_week.geojson") and len(t) == 2
    assert t.column("mag").tolist() == [4.5, 6.5] and t.column("depth_km").tolist() == [10.0, 30.0]
    assert t.column("strength").tolist() == pytest.approx([1.0, 10.0])
    assert t.stats == {"strongest": 6.5} and "Geological Survey" in t.attribution
    made = datetime.fromtimestamp(1795645454, timezone.utc)
    assert t.times["quakes_to"] == made and (made - t.times["quakes_from"]).days == 7
    for feed in ("4.5_year", "big_week", "week"):
        with pytest.raises(RecipeError, match="feed is <level>_<span>"):
            load_source({"type": "usgs_quakes", "feed": feed}, ctx_for(tmp_path))
    doc["features"] = []
    with pytest.raises(RecipeError, match="no earthquakes"):
        load_source({"type": "usgs_quakes", "max_age_minutes": 0}, ctx_for(tmp_path))
    with pytest.raises(RecipeError, match="unexpected USGS feed"):
        usgs.parse_feed({"features": []})
