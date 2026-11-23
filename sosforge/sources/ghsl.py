"""GHSL Urban Centre Database R2024A (European Commission JRC, CC BY 4.0): about 11,700 urban centres with population
every five years from 1975 to 2030 (2030 projected). Read from its GeoPackage with sqlite3; the points are in World
Mollweide (ESRI:54009), inverted to latitude/longitude here, so no GIS library is needed."""
from __future__ import annotations

import logging
import math
import sqlite3
import struct
import zipfile
from pathlib import Path

import numpy as np

from ..fetch import Asset, cache_root, ensure_asset
from ..recipe import RecipeError, check_keys
from ..registry import register_source
from .base import PointTable

LOG = logging.getLogger("sosforge.ghsl")
UCDB = Asset("ghsl_ucdb_mtuc_r2024a",
             "https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/GHSL/GHS_UCDB_GLOBE_R2024A/GHS_UCDB_MTUC_GLOBE_R2024A/V1-2/"
             "GHS_UCDB_MTUC_GLOBE_R2024A_V1_2.zip", "GHS_UCDB_MTUC_GLOBE_R2024A_V1_2.zip", 37155214,
             "76eddb0e609a2a6c9d9266d62ddab5f5f1f0b457c68291ea9755b1e06855f32f")
TABLE = "GHSL_UCDB_MTUC_GLOBE_R2024"
GPKG = "GHS_UCDB_MTUC_GLOBE_R2024A.gpkg"
ATTRIBUTION = ("GHSL Urban Centre Database R2024A, European Commission Joint Research Centre, "
               "doi:10.2905/1a338be6-7eaf-480c-9664-3a8ade88cbcd (CC BY 4.0)")
R_MOLLWEIDE = 6378137.0
ENVELOPE_DOUBLES = {0: 0, 1: 4, 2: 6, 3: 6, 4: 8}


def inverse_mollweide(x: np.ndarray, y: np.ndarray, radius: float = R_MOLLWEIDE) -> tuple[np.ndarray, np.ndarray]:
    """Convert World Mollweide metres to latitude and longitude in degrees (sphere of the given radius, central meridian 0). Tested by test_v3_sources.py::test_mollweide_and_gpkg_points."""
    theta = np.arcsin(np.clip(y / (math.sqrt(2.0) * radius), -1.0, 1.0))
    lat = np.arcsin(np.clip((2 * theta + np.sin(2 * theta)) / math.pi, -1.0, 1.0))
    lon = math.pi * x / (2 * math.sqrt(2.0) * radius * np.maximum(np.cos(theta), 1e-12))
    return np.degrees(lat), np.degrees(lon)


def gpkg_point(blob: bytes) -> tuple[float, float]:
    """Read x and y from a GeoPackage geometry blob holding a 2-D point. Tested by test_v3_sources.py::test_mollweide_and_gpkg_points."""
    if blob[:2] != b"GP":
        raise RecipeError("not a GeoPackage geometry")
    flags = blob[3]
    off = 8 + 8 * ENVELOPE_DOUBLES[(flags >> 1) & 7]
    order = "<" if blob[off] == 1 else ">"
    kind = struct.unpack(order + "I", blob[off + 1:off + 5])[0] % 1000
    if kind != 1:
        raise RecipeError(f"expected a point geometry, got WKB type {kind}")
    return struct.unpack(order + "dd", blob[off + 5:off + 21])


def read_points(gpkg: Path, table: str = TABLE) -> dict[str, np.ndarray]:
    """Read every urban centre's name, country, location and MT_POP_TOT_<year> columns from the GeoPackage. Tested by test_v3_sources.py::test_mollweide_and_gpkg_points."""
    con = sqlite3.connect(f"file:{gpkg}?mode=ro", uri=True)
    try:
        cols = [r[1] for r in con.execute(f'pragma table_info("{table}")')]
        pops = sorted(c for c in cols if c.startswith("MT_POP_TOT_"))
        if not pops:
            raise RecipeError(f"{gpkg.name} has no MT_POP_TOT_<year> columns in {table}")
        sel = ", ".join(f'"{c}"' for c in ["GC_UCN_MAI_2025", "GC_CNT_GAD_2025", "geom", *pops])
        rows = con.execute(f'select {sel} from "{table}"').fetchall()
    finally:
        con.close()
    xy = np.array([gpkg_point(r[2]) for r in rows])
    lat, lon = inverse_mollweide(xy[:, 0], xy[:, 1])
    out = {"lat": lat, "lon": lon, "name": np.array([r[0] or "" for r in rows], dtype=object),
           "country": np.array([r[1] or "" for r in rows], dtype=object)}
    for i, c in enumerate(pops):
        out[c] = np.array([float(r[3 + i] or 0) for r in rows])
    return out


@register_source("ghsl_ucdb")
def ghsl_ucdb(opts: dict, ctx) -> PointTable:
    """Download (once, size- and hash-checked) the GHSL urban centres and return them as points with population columns. Tested by test_v3_sources.py::test_mollweide_and_gpkg_points through read_points; the download is exercised by real builds."""
    check_keys("source ghsl_ucdb", opts, set())
    zpath = ensure_asset(UCDB)
    gpkg = cache_root() / "assets" / "ghsl_ucdb" / GPKG
    if not gpkg.exists():
        gpkg.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zpath) as z:
            with z.open(GPKG) as src, open(gpkg.with_suffix(".part"), "wb") as dst:
                dst.write(src.read())
        gpkg.with_suffix(".part").replace(gpkg)
    cols = read_points(gpkg)
    LOG.info("ghsl: %d urban centres", len(cols["lat"]))
    return PointTable(cols, ATTRIBUTION, "CC-BY-4.0", UCDB.sha256)
