"""GHSL, the European Commission JRC's Global Human Settlement Layer (CC BY 4.0), in two sources.

ghsl_ucdb: the Urban Centre Database R2024A, about 11,700 urban centres with population every five years from 1975 to
2030 (2030 projected). Read from its GeoPackage with sqlite3; the points are in World Mollweide (ESRI:54009), inverted
to latitude/longitude here, so no GIS library is needed.

ghsl_pop: the GHS-POP R2023A population grid for 2025 at 30 arcseconds, summed into coarser cells as people per square
kilometre (design/v0/v0.8.6.md). Read tile by tile from its GeoTIFF with tifffile, so the 7 GB grid never sits in
memory."""
from __future__ import annotations

import logging
import math
import sqlite3
import struct
import zipfile
from pathlib import Path

import numpy as np
import tifffile

from ..fetch import Asset, cache_root, ensure_asset
from ..recipe import RecipeError, check_keys
from ..registry import register_source
from .base import GridField, PointTable

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


POP_2025 = Asset("ghsl_pop_e2025_r2023a_30ss",
                 "https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/GHSL/GHS_POP_GLOBE_R2023A/GHS_POP_E2025_GLOBE_R2023A_4326_30ss/"
                 "V1-0/GHS_POP_E2025_GLOBE_R2023A_4326_30ss_V1_0.zip", "GHS_POP_E2025_GLOBE_R2023A_4326_30ss_V1_0.zip",
                 483694490, "cec59a706722493bc670bb53503ce4a2f9b9850aab76dbf9f21fb94746d01195")
POP_TIF = "GHS_POP_E2025_GLOBE_R2023A_4326_30ss_V1_0.tif"
POP_ATTRIBUTION = ("GHS-POP R2023A, 2025 estimate, European Commission Joint Research Centre, "
                   "doi:10.2905/2FF68A52-5B5B-4A22-8F40-C41DA8332CFE (CC BY 4.0)")
EARTH_RADIUS_KM = 6371.0088


def runs(index: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Return where each run of equal values starts in a non-decreasing index array, and the value of each run. Tested by test_v3_sources.py::test_ghsl_pop_sums_people through population_counts."""
    starts = np.flatnonzero(np.r_[True, index[1:] != index[:-1]])
    return starts, index[starts]


def population_counts(tif: Path, cells_per_degree: int = 12) -> np.ndarray:
    """Sum a GHS-POP GeoTIFF's people per cell into a whole-globe grid of 1/cells_per_degree degree cells, row 0 at 90N and column 0 at 180W, giving each source cell to the cell holding its centre and dropping source columns centred beyond +/-180, read tile by tile. Tested by test_v3_sources.py::test_ghsl_pop_sums_people with a small GeoTIFF of known people."""
    with tifffile.TiffFile(tif) as t:
        page = t.pages[0]
        sx, sy = page.tags["ModelPixelScaleTag"].value[:2]
        tie = page.tags["ModelTiepointTag"].value
        lon0, lat0 = tie[3] - tie[0] * sx, tie[4] + tie[1] * sy
        height, width = page.shape[:2]
        rows, cols = 180 * cells_per_degree, 360 * cells_per_degree
        lat_c = lat0 - (np.arange(height) + 0.5) * sy
        lon_c = lon0 + (np.arange(width) + 0.5) * sx
        row_of = np.clip(np.floor((90.0 - lat_c) * cells_per_degree).astype(np.int64), 0, rows - 1)
        col_of = np.clip(np.floor((lon_c + 180.0) * cells_per_degree).astype(np.int64), 0, cols - 1)
        outside = (lon_c < -180.0) | (lon_c >= 180.0)
        out = np.zeros((rows, cols), np.float64)
        for data, index, _ in page.segments():
            y, x = index[-3], index[-2]
            h, w = min(data.shape[1], height - y), min(data.shape[2], width - x)
            tile = np.array(data[0, :h, :w, 0], np.float64)
            tile[~(tile > 0)] = 0.0                 # no data, negatives and NaN count as nobody
            tile[:, outside[x:x + w]] = 0.0
            rs, rv = runs(row_of[y:y + h])
            cs, cv = runs(col_of[x:x + w])
            out[np.ix_(rv, cv)] += np.add.reduceat(np.add.reduceat(tile, rs, axis=0), cs, axis=1)
    return out


def cell_areas_km2(cells_per_degree: int, radius_km: float = EARTH_RADIUS_KM) -> np.ndarray:
    """Return the area in square kilometres of one cell in each row of a whole-globe grid, row 0 at 90N. Tested by test_v3_sources.py::test_ghsl_pop_sums_people: the rows add up to the sphere's area."""
    d = 1.0 / cells_per_degree
    top = np.radians(90.0 - np.arange(180 * cells_per_degree) * d)
    return radius_km ** 2 * np.radians(d) * (np.sin(top) - np.sin(top - np.radians(d)))


def population_field(counts: np.ndarray, cells_per_degree: int, fingerprint: str) -> GridField:
    """Turn people per cell into a GridField of people per square kilometre at cell centres, with the total for HUD templates. Tested by test_v3_sources.py::test_ghsl_pop_sums_people."""
    d = 1.0 / cells_per_degree
    total = float(counts.sum())
    density = (counts / cell_areas_km2(cells_per_degree)[:, None]).astype(np.float32)
    return GridField(density, -180.0 + d / 2, d, 90.0 - d / 2, -d, attribution=POP_ATTRIBUTION, license="CC-BY-4.0",
                     fingerprint=f"{fingerprint}:{cells_per_degree}",
                     stats={"people": total, "people_billions": total / 1e9})


@register_source("ghsl_pop")
def ghsl_pop(opts: dict, ctx) -> GridField:
    """Download (once, size- and hash-checked) GHSL's 2025 population grid and return people per square kilometre on cells of 1/cells_per_degree degrees (default 12), keeping the extracted GeoTIFF and the summed cells in the cache. Tested by test_v3_sources.py::test_ghsl_pop_sums_people through population_counts and population_field; the download is exercised by real builds."""
    check_keys("source ghsl_pop", opts, {"cells_per_degree"})
    cpd = int(opts.get("cells_per_degree", 12))
    if not 1 <= cpd <= 120:
        raise RecipeError("source ghsl_pop: cells_per_degree must be 1 to 120 (120 is the grid's own 30 arcseconds)")
    folder = cache_root() / "ghsl_pop"
    counts_path = folder / f"counts_{POP_2025.sha256[:12]}_{cpd}.npy"
    if not counts_path.exists():
        tif = folder / POP_TIF
        if not tif.exists():
            zpath = ensure_asset(POP_2025)
            folder.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(zpath) as z, z.open(POP_TIF) as src, open(tif.with_suffix(".part"), "wb") as dst:
                while chunk := src.read(1 << 24):
                    dst.write(chunk)
            tif.with_suffix(".part").replace(tif)
        LOG.info("ghsl: summing %s into %d cells per degree", tif.name, cpd)
        counts = population_counts(tif, cpd)
        np.save(counts_path.with_suffix(".part.npy"), counts)
        counts_path.with_suffix(".part.npy").replace(counts_path)
    counts = np.load(counts_path)
    LOG.info("ghsl: %.3f billion people", counts.sum() / 1e9)
    return population_field(counts, cpd, POP_2025.sha256)
