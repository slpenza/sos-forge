"""Named NASA basemaps fetched as equirectangular (EPSG:4326) images from NASA GIBS WMS at the exact output size."""
from __future__ import annotations

import logging
from pathlib import Path

import cv2
import numpy as np

from .fetch import cached_call, http_request

LOG = logging.getLogger("sosforge.basemaps")
GIBS_WMS = "https://gibs.earthdata.nasa.gov/wms/epsg4326/best/wms.cgi"
GIBS_CREDIT = "NASA Global Imagery Browse Services (GIBS), part of NASA's ESDIS"
MAX_WMS_WIDTH = 8192
BASEMAPS = {
    "black_marble": ("VIIRS_Black_Marble", "NASA Black Marble 2016 night lights (Suomi NPP VIIRS)"),
    "blue_marble": ("BlueMarble_NextGeneration", "NASA Blue Marble: Next Generation"),
    "blue_marble_relief": ("BlueMarble_ShadedRelief_Bathymetry", "NASA Blue Marble shaded relief and bathymetry"),
}


def basemap_credit(name: str) -> str:
    """Return the attribution line for a named basemap. Tested by test_layers.py::test_basemap_named_uses_gibs_request."""
    return f"{BASEMAPS[name][1]}, via {GIBS_CREDIT}"


def wms_params(layer: str, width: int) -> dict:
    """Return GIBS WMS GetMap parameters for a whole-globe 2:1 request. Tested by test_layers.py::test_basemap_named_uses_gibs_request."""
    fmt = "image/png" if width <= 4096 else "image/jpeg"
    return {"SERVICE": "WMS", "REQUEST": "GetMap", "VERSION": "1.1.1", "LAYERS": layer, "SRS": "EPSG:4326",
            "BBOX": "-180,-90,180,90", "WIDTH": width, "HEIGHT": width // 2, "FORMAT": fmt, "STYLES": ""}


def fetch_basemap(name: str, width: int, refresh: bool = False) -> Path:
    """Download (or reuse) a named basemap at min(width, 8192) pixels wide and verify it decodes at 2:1. Tested by test_layers.py::test_basemap_named_uses_gibs_request with a stubbed HTTP call."""
    if name not in BASEMAPS:
        raise KeyError(f"unknown basemap '{name}' (choose from {', '.join(sorted(BASEMAPS))})")
    req_w = min(width, MAX_WMS_WIDTH)
    params = wms_params(BASEMAPS[name][0], req_w)
    suffix = ".png" if params["FORMAT"] == "image/png" else ".jpg"
    path = cached_call("gibs", params, lambda: http_request(GIBS_WMS, params=params, timeout=300), suffix=suffix,
                       max_age_days=3650, refresh=refresh)
    img = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if img is None or img.shape[:2] != (req_w // 2, req_w):
        path.unlink(missing_ok=True)
        raise RuntimeError(f"GIBS returned an unusable image for {name} at {req_w}px (not a {req_w}x{req_w // 2} image)")
    LOG.info("basemap %s %dx%d ready (%s)", name, req_w, req_w // 2, path.name)
    return path


def load_rgb(path: Path) -> np.ndarray:
    """Load any raster as float RGB in [0, 1] (TIFF via tifffile, other formats via Pillow). Tested by test_layers.py::test_basemap_from_path_and_color."""
    path = Path(path)
    if path.suffix.lower() in (".tif", ".tiff"):
        import tifffile
        img = tifffile.imread(str(path))
    else:
        from PIL import Image
        with Image.open(path) as im:
            img = np.asarray(im.convert("RGB") if im.mode not in ("RGB", "I;16", "I;16B") else im)
    if img.ndim == 3 and img.shape[0] in (3, 4) and img.shape[-1] not in (3, 4):
        img = np.moveaxis(img, 0, -1)
    if img.ndim == 2:
        img = np.repeat(img[..., None], 3, axis=-1)
    img = img[..., :3]
    if np.issubdtype(img.dtype, np.integer):
        return img.astype(np.float32) / float(np.iinfo(img.dtype).max)
    return np.clip(img.astype(np.float32), 0.0, 1.0)
