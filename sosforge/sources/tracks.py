"""Activity tracks from files: GPX, TCX and FIT (each optionally .gz) plus GeoJSON LineStrings.

A fitness app's bulk export (GPX, TCX or FIT files, often gzipped) is read as-is.
FIT needs the optional `fitdecode` package (pip install "sos-forge[fit]").
"""
from __future__ import annotations

import glob
import gzip
import io
import json
import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from ..recipe import RecipeError, check_keys
from ..registry import register_source
from .base import Track, TrackSet, digest_bytes

LOG = logging.getLogger("sosforge.tracks")
SEMICIRCLE = 180.0 / 2 ** 31
SUFFIXES = (".gpx", ".tcx", ".fit", ".geojson", ".json")


def _local(tag: str) -> str:
    """Strip an XML namespace from a tag name. Tested indirectly by test_sources.py::test_track_formats_parse."""
    return tag.rsplit("}", 1)[-1]


def _iso_seconds(text: str | None) -> float:
    """Parse an ISO timestamp to epoch seconds, returning NaN when absent or malformed. Tested indirectly by test_sources.py::test_track_formats_parse."""
    if not text:
        return float("nan")
    try:
        dt = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    except ValueError:
        return float("nan")
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).timestamp()


def parse_gpx(data: bytes, name: str) -> list[Track]:
    """Parse every <trk> (and <rte>) in a GPX document into Tracks with times when present. Tested by test_sources.py::test_track_formats_parse."""
    root = ET.fromstring(data)
    sport = next((e.text or "" for e in root.iter() if _local(e.tag) == "type"), "")
    out = []
    for trk in [e for e in root.iter() if _local(e.tag) in ("trk", "rte")]:
        lat, lon, tm = [], [], []
        for pt in trk.iter():
            if _local(pt.tag) in ("trkpt", "rtept"):
                lat.append(float(pt.get("lat")))
                lon.append(float(pt.get("lon")))
                tm.append(_iso_seconds(next((c.text for c in pt if _local(c.tag) == "time"), None)))
        if len(lat) >= 2:
            out.append(Track(np.array(lat), np.array(lon), np.array(tm), name, sport))
    return out


def parse_tcx(data: bytes, name: str) -> list[Track]:
    """Parse each TCX Activity's Trackpoints that carry a Position into a Track. Tested by test_sources.py::test_track_formats_parse."""
    root = ET.fromstring(data.strip())
    out = []
    for act in [e for e in root.iter() if _local(e.tag) == "Activity"]:
        lat, lon, tm = [], [], []
        for tp in [e for e in act.iter() if _local(e.tag) == "Trackpoint"]:
            vals = {_local(e.tag): e.text for e in tp.iter()}
            if vals.get("LatitudeDegrees") and vals.get("LongitudeDegrees"):
                lat.append(float(vals["LatitudeDegrees"]))
                lon.append(float(vals["LongitudeDegrees"]))
                tm.append(_iso_seconds(vals.get("Time")))
        if len(lat) >= 2:
            out.append(Track(np.array(lat), np.array(lon), np.array(tm), name, act.get("Sport", "")))
    return out


def parse_fit(data: bytes, name: str) -> list[Track]:
    """Parse FIT record messages (semicircle positions) into one Track using the optional fitdecode package. Tested only when fitdecode is installed; skipped otherwise."""
    try:
        import fitdecode
    except ImportError as exc:
        raise RecipeError("reading .fit files needs the optional dependency: pip install 'sos-forge[fit]'") from exc
    lat, lon, tm, sport = [], [], [], ""
    with fitdecode.FitReader(io.BytesIO(data)) as reader:
        for frame in reader:
            if getattr(frame, "frame_type", None) != fitdecode.FIT_FRAME_DATA:
                continue
            if frame.name == "sport" and frame.has_field("sport"):
                sport = str(frame.get_value("sport"))
            if frame.name == "record" and frame.has_field("position_lat") and frame.has_field("position_long"):
                la, lo = frame.get_value("position_lat"), frame.get_value("position_long")
                if la is None or lo is None:
                    continue
                lat.append(la * SEMICIRCLE)
                lon.append(lo * SEMICIRCLE)
                ts = frame.get_value("timestamp") if frame.has_field("timestamp") else None
                tm.append(ts.timestamp() if ts else float("nan"))
    return [Track(np.array(lat), np.array(lon), np.array(tm), name, sport)] if len(lat) >= 2 else []


def parse_geojson_lines(data: bytes, name: str) -> list[Track]:
    """Parse LineString and MultiLineString features into Tracks. Tested by test_sources.py::test_track_formats_parse."""
    doc = json.loads(data)
    out = []
    for f in doc.get("features", []):
        g = f.get("geometry") or {}
        lines = [g["coordinates"]] if g.get("type") == "LineString" else g.get("coordinates", []) if g.get("type") == "MultiLineString" else []
        for line in lines:
            if len(line) >= 2:
                arr = np.asarray(line, dtype=float)
                out.append(Track(arr[:, 1], arr[:, 0], None, (f.get("properties") or {}).get("name", name)))
    return out


PARSERS = {".gpx": parse_gpx, ".tcx": parse_tcx, ".fit": parse_fit, ".geojson": parse_geojson_lines,
           ".json": parse_geojson_lines}


def read_track_file(path: Path) -> list[Track]:
    """Read one activity file, transparently gunzipping .gz, and dispatch on its inner suffix. Tested by test_sources.py::test_track_formats_parse."""
    data = path.read_bytes()
    inner = path.name[:-3] if path.name.endswith(".gz") else path.name
    if path.name.endswith(".gz"):
        data = gzip.decompress(data)
    suffix = Path(inner).suffix.lower()
    return PARSERS[suffix](data, Path(inner).stem)


@register_source("tracks")
def tracks_source(opts: dict, ctx) -> TrackSet:
    """Load every supported activity file matching a recipe-relative glob, skipping unreadable files with a warning. Tested by test_sources.py::test_track_formats_parse."""
    check_keys("source tracks", opts, {"path", "attribution", "license"})
    pattern = str(ctx.recipe.resolve(opts.get("path", "data/**/*")))
    files = sorted(p for p in map(Path, glob.glob(pattern, recursive=True))
                   if p.is_file() and (p.suffix.lower() in SUFFIXES or p.name.lower().endswith(tuple(s + ".gz" for s in SUFFIXES))))
    if not files:
        raise RecipeError(f"no activity files match {pattern}. Export your activities from your fitness app or GPS "
                          f"watch (GPX, TCX or FIT, zipped or not) and copy them there")
    tracks, skipped, digest = [], 0, []
    for p in files:
        try:
            tracks.extend(read_track_file(p))
            digest.append(f"{p.name}:{p.stat().st_size}:{int(p.stat().st_mtime)}".encode())
        except RecipeError:
            raise
        except Exception as exc:  # noqa: BLE001 - one corrupt export file should not sink the build
            skipped += 1
            LOG.warning("skipping %s: %s", p.name, exc)
    LOG.info("tracks: %d activities from %d files (%d skipped)", len(tracks), len(files), skipped)
    return TrackSet(tracks, opts.get("attribution", ""), opts.get("license", ""), digest_bytes(*digest))
