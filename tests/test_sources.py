"""Tests for built-in data sources with local files and stubbed HTTP."""
from __future__ import annotations

import gzip
import json
from types import SimpleNamespace

import numpy as np
import pytest

from conftest import gpx_loop, write_recipe
from sosforge.recipe import RecipeError, load_recipe
from sosforge.sources import load_source
from sosforge.sources import overpass as op
from sosforge.sources import pinballmap as pbm
from sosforge.sources.base import to_epoch_days

RECIPE = '[dataset]\nname="s"\n[[layers]]\ntype="basemap"\ncolor="#000000"\n'


def ctx_for(tmp_path, files=None, refresh=False):
    """Return a minimal context with a recipe rooted in a temp folder. Helper; not itself tested."""
    return SimpleNamespace(recipe=load_recipe(write_recipe(tmp_path / "r", RECIPE, files)), refresh=refresh)


def test_csv_and_geojson_points(tmp_path):
    """Checks CSV columns convert to numbers or text, lat/lon can be renamed, and GeoJSON points flatten properties. Covers csv_source, geojson_source, numeric_or_text and PointTable.column; this test is not itself tested."""
    gj = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [10, 20]}, "properties": {"n": 3}},
        {"type": "Feature", "geometry": {"type": "MultiPoint", "coordinates": [[1, 2], [3, 4]]}, "properties": {"n": 1}}]}
    ctx = ctx_for(tmp_path, {"d/x.csv": "Y,X,name,score\n1.5,2.5,a,7\n-3,4,b,\n", "d/x.geojson": json.dumps(gj)})
    t = load_source({"type": "csv", "path": "d/x.csv", "lat": "Y", "lon": "X"}, ctx)
    assert len(t) == 2 and t.columns["lat"][0] == 1.5 and np.isnan(t.columns["score"][1])
    assert t.column("name").dtype == object and t.fingerprint
    with pytest.raises(RecipeError, match="available"):
        t.column("nope")
    g = load_source({"type": "geojson", "path": "d/x.geojson"}, ctx)
    assert g.columns["lat"].tolist() == [20, 2, 4] and g.columns["n"].tolist() == [3, 1, 1]


def test_unknown_source_type(tmp_path):
    """Checks an unknown or missing source type gives an actionable error. Covers load_source; this test is not itself tested."""
    with pytest.raises(RecipeError, match="available"):
        load_source({"type": "nope"}, ctx_for(tmp_path))
    with pytest.raises(RecipeError, match="must be a table"):
        load_source("x.csv", ctx_for(tmp_path))


def test_to_epoch_days():
    """Checks ISO dates, Z timestamps, numbers and blanks convert to epoch days. Covers to_epoch_days; this test is not itself tested."""
    out = to_epoch_days(["1970-01-02", "1970-01-01T12:00:00Z", 5, "", None])
    assert out[:3].tolist() == [1.0, 0.5, 5.0] and np.isnan(out[3]) and np.isnan(out[4])


def test_track_formats_parse(tmp_path):
    """Checks GPX, gzipped GPX, TCX and GeoJSON line files all load as tracks and an empty folder explains how to export activities. Covers tracks_source, read_track_file, parse_gpx, parse_tcx and parse_geojson_lines; FIT is not tested without fitdecode."""
    tcx = ('<TrainingCenterDatabase xmlns="http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2"><Activities>'
           '<Activity Sport="Biking"><Lap><Track>'
           + "".join(f"<Trackpoint><Time>2024-01-01T00:00:0{i}Z</Time><Position><LatitudeDegrees>{45 + i * 1e-3}"
                     f"</LatitudeDegrees><LongitudeDegrees>{7 + i * 1e-3}</LongitudeDegrees></Position></Trackpoint>"
                     for i in range(5)) + "</Track></Lap></Activity></Activities></TrainingCenterDatabase>")
    lines = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {"name": "ride"},
             "geometry": {"type": "LineString", "coordinates": [[0, 0], [1, 1], [2, 1]]}}]}
    ctx = ctx_for(tmp_path, {"data/a.gpx": gpx_loop(47.6, -122.3), "data/b.gpx.gz": gzip.compress(gpx_loop(51.5, -0.1).encode()),
                             "data/c.tcx": tcx, "data/d.geojson": json.dumps(lines), "data/notes.txt": "ignore me"})
    ts = load_source({"type": "tracks", "path": "data/*"}, ctx)
    assert len(ts.tracks) == 4 and ts.fingerprint
    assert {t.sport for t in ts.tracks} >= {"running", "Biking"}
    gpx = next(t for t in ts.tracks if t.sport == "running" and abs(t.lat.mean() - 47.6) < 0.1)
    assert len(gpx.lat) == 121 and np.isfinite(gpx.time).all()
    with pytest.raises(RecipeError, match="Export your activities"):
        load_source({"type": "tracks", "path": "empty/*"}, ctx)


def test_overpass_query_parse_and_cache(tmp_path, monkeypatch):
    """Checks queries are wrapped as CSV with centres, results parse per category, error pages are rejected, and the second run hits the cache. Covers overpass_source, build_query and parse_csv with stubbed HTTP; the live server is exercised only by real builds."""
    sent = []

    def fake(url, data=None, **kw):
        """Return two rows for any query. Helper; not itself tested."""
        sent.append(data)
        return f"{op.HEADER}\n10.5\t20.25\t1\tway\n-5\t100\t2\tnode\n".encode()

    monkeypatch.setattr(op, "http_request", fake)
    monkeypatch.setattr(op.time, "sleep", lambda s: None)
    spec = {"type": "overpass", "queries": [{"category": "A", "query": 'nwr["sport"="cricket"]'},
                                            {"category": "B", "query": 'nwr["leisure"="golf_course"];'}]}
    t = load_source(spec, ctx_for(tmp_path))
    assert len(t) == 4 and t.columns["category"].tolist() == ["A", "A", "B", "B"] and "OpenStreetMap" in t.attribution
    assert b"out%3Acsv" in sent[0] and b"out%20center" in sent[0]
    load_source(spec, ctx_for(tmp_path))
    assert len(sent) == 2
    with pytest.raises(RuntimeError, match="did not return CSV"):
        op.parse_csv(b"<html>502 Gateway Timeout</html>", "q")
    assert "out center;" in op.build_query('nwr["a"="b"];', 60) and "[timeout:60]" in op.build_query("x", 60)


def test_pinballmap_token_required(tmp_path, monkeypatch):
    """Checks a missing token fails with signup instructions and the token is sent only as a header. Covers pinballmap_source token handling with stubbed HTTP; the live API is untested pending a token."""
    monkeypatch.delenv("PINBALLMAP_API_TOKEN", raising=False)
    with pytest.raises(RecipeError, match="api_token"):
        load_source({"type": "pinballmap"}, ctx_for(tmp_path))
    seen = {}

    def fake(url, params=None, headers=None, **kw):
        """Record the call and return one location. Helper; not itself tested."""
        seen.update(url=url, headers=headers, params=params)
        return json.dumps({"locations": [{"id": 1, "name": "Ground Kontrol", "lat": "45.52", "lon": "-122.67",
                                          "num_machines": 30, "created_at": "2010-05-01T00:00:00Z"}]}).encode()

    monkeypatch.setenv("PINBALLMAP_API_TOKEN", "secret-token")
    monkeypatch.setattr(pbm, "http_request", fake)
    t = load_source({"type": "pinballmap"}, ctx_for(tmp_path))
    assert seen["headers"]["X-Api-Token"] == "secret-token" and "secret" not in seen["url"]
    assert len(t) == 1 and t.license == "CC-BY-SA-4.0"
    cached = list((tmp_path / "cache" / "sources" / "pinballmap").glob("*.key.json"))
    assert cached and "secret" not in cached[0].read_text()


def test_pinballmap_normalises_fields():
    """Checks alternative field names, machine lists and bad payloads are handled. Covers normalise; this test is not itself tested."""
    cols = pbm.normalise([{"latitude": 1, "longitude": 2, "machine_names": ["a", "b"]}, {"lat": None, "lon": 3}])
    assert cols["lat"].tolist() == [1.0] and cols["num_machines"].tolist() == [2.0]
    with pytest.raises(RuntimeError, match="unexpected"):
        pbm.normalise({"error": "x"})
    with pytest.raises(RuntimeError, match="no coordinates"):
        pbm.normalise([{"x": 1}])
