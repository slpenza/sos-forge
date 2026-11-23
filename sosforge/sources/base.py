"""Source result types and the dispatcher that turns a recipe `source = {type = ...}` table into data."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone

import numpy as np

from ..recipe import RecipeError
from ..registry import SOURCES


@dataclass
class PointTable:
    """Columnar point data with lat/lon in degrees plus any extra columns, and its attribution. Tested by test_sources.py."""

    columns: dict[str, np.ndarray]
    attribution: str = ""
    license: str = ""
    fingerprint: str = ""

    def __len__(self) -> int:
        """Return the number of rows. Tested by test_sources.py::test_csv_and_geojson_points."""
        return len(self.columns["lat"])

    def column(self, name: str) -> np.ndarray:
        """Return a column by name or raise a RecipeError listing the available columns. Tested by test_sources.py::test_csv_and_geojson_points."""
        if name not in self.columns:
            raise RecipeError(f"column '{name}' not found; available: {', '.join(sorted(self.columns))}")
        return self.columns[name]


@dataclass
class Track:
    """One activity: ordered lat/lon degrees, optional epoch-second times, a name and a sport. Tested by test_sources.py::test_track_formats_parse."""

    lat: np.ndarray
    lon: np.ndarray
    time: np.ndarray | None = None
    name: str = ""
    sport: str = ""


@dataclass
class TrackSet:
    """A collection of tracks with attribution. Tested by test_sources.py::test_track_formats_parse."""

    tracks: list[Track] = field(default_factory=list)
    attribution: str = ""
    license: str = ""
    fingerprint: str = ""


def load_source(spec: dict, ctx):
    """Dispatch a recipe source table to its registered loader with clear errors for unknown types. Tested by test_sources.py::test_unknown_source_type."""
    if not isinstance(spec, dict) or "type" not in spec:
        raise RecipeError("a layer 'source' must be a table with a 'type', for example { type = \"csv\", path = \"x.csv\" }")
    kind = spec["type"]
    if kind not in SOURCES:
        raise RecipeError(f"unknown source type '{kind}' (available: {', '.join(sorted(SOURCES))})")
    opts = {k: v for k, v in spec.items() if k != "type"}
    return SOURCES[kind](opts, ctx)


def digest_bytes(*chunks: bytes) -> str:
    """Return a short SHA-1 fingerprint of raw source bytes. Tested indirectly by test_sources.py::test_csv_and_geojson_points."""
    h = hashlib.sha1()
    for c in chunks:
        h.update(c)
    return h.hexdigest()[:16]


def to_epoch_days(values) -> np.ndarray:
    """Convert ISO date strings, datetimes or numbers to float days since 1970-01-01 UTC, with NaN for blanks. Tested by test_sources.py::test_to_epoch_days."""
    out = np.full(len(values), np.nan)
    for i, v in enumerate(values):
        if v is None or (isinstance(v, float) and np.isnan(v)) or v == "":
            continue
        if isinstance(v, (int, float, np.integer, np.floating)):
            out[i] = float(v)
            continue
        if isinstance(v, datetime):
            dt = v
        else:
            s = str(v).strip().replace("Z", "+00:00")
            dt = datetime.fromisoformat(s if "T" in s or len(s) > 10 else s[:10])
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        out[i] = dt.timestamp() / 86400.0
    return out


def numeric_or_text(values: list) -> np.ndarray:
    """Return a float array when every non-blank value parses as a number, otherwise an object array of strings. Tested by test_sources.py::test_csv_and_geojson_points."""
    try:
        return np.array([float(v) if v not in ("", None) else np.nan for v in values], dtype=np.float64)
    except (TypeError, ValueError):
        return np.array(["" if v is None else str(v) for v in values], dtype=object)


@dataclass
class DataTable:
    """Columnar data without coordinates, such as country-by-year values. Tested by test_v3_sources.py::test_owid_and_table_sources."""

    columns: dict[str, np.ndarray]
    attribution: str = ""
    license: str = ""
    fingerprint: str = ""

    def column(self, name: str) -> np.ndarray:
        """Return a column by name or raise a RecipeError listing the available columns. Tested by test_v3_sources.py::test_owid_and_table_sources."""
        if name not in self.columns:
            raise RecipeError(f"column '{name}' not found; available: {', '.join(sorted(self.columns))}")
        return self.columns[name]


@dataclass
class GridField:
    """A regular latitude/longitude grid of values: row 0 at lat0, column 0 at lon0, steps in degrees, plus named times. Tested by test_v3_sources.py::test_swpc_aurora_parses_grid."""

    values: np.ndarray
    lon0: float
    dlon: float
    lat0: float
    dlat: float
    times: dict = field(default_factory=dict)
    attribution: str = ""
    license: str = ""
    fingerprint: str = ""


@dataclass
class RegionSet:
    """Named regions with codes and outer rings as (N, 2) lon/lat arrays, for shading by value. Tested by test_v3_sources.py::test_natural_earth_and_geojson_regions."""

    codes: list[str]
    names: list[str]
    rings: list[list[np.ndarray]]
    attribution: str = ""
    license: str = ""
    fingerprint: str = ""
