"""Shared fixtures: an isolated cache per test, recipe-folder writers, and synthetic basemap/GPX generators."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import cv2
import numpy as np
import pytest

REAL_CACHE = Path(os.environ.get("SOSFORGE_CACHE") or Path.home() / ".cache" / "sosforge")
REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    """Point $SOSFORGE_CACHE at a per-test folder so tests never touch real downloads. Fixture; not itself tested."""
    monkeypatch.setenv("SOSFORGE_CACHE", str(tmp_path / "cache"))
    yield


def write_recipe(root: Path, toml: str, files: dict | None = None) -> Path:
    """Write recipe.toml plus extra files (bytes or text) into a folder and return the folder. Helper; not itself tested."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "recipe.toml").write_text(toml)
    for name, content in (files or {}).items():
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content if isinstance(content, bytes) else content.encode())
    return root


def earth_png(path: Path, width: int = 512) -> Path:
    """Write a seamless (periodic in longitude) synthetic 2:1 basemap PNG. Helper; not itself tested."""
    h = width // 2
    lon = ((np.arange(width) + 0.5) / width) * 2 * np.pi - np.pi
    lat = np.pi / 2 - ((np.arange(h) + 0.5) / h) * np.pi
    base = 0.35 + 0.15 * np.cos(lon)[None, :] * np.cos(lat)[:, None] + 0.1 * np.sin(3 * lon)[None, :]
    img = np.clip(np.dstack([base, base * 0.9, base * 0.7]) * 255, 0, 255).astype(np.uint8)
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), img[..., ::-1])
    return path


def gpx_loop(lat0: float, lon0: float, radius_m: float = 3000.0, n: int = 120, start: str = "2025-05-01T07:00:00Z",
             sport: str = "running") -> str:
    """Return a GPX document with one closed loop track around a centre, with one-second timestamps. Helper; not itself tested."""
    t0 = datetime.fromisoformat(start.replace("Z", "+00:00"))
    pts = []
    for i in range(n + 1):
        a = 2 * np.pi * i / n
        la = lat0 + (radius_m / 111_195.0) * np.sin(a)
        lo = lon0 + (radius_m / (111_195.0 * np.cos(np.radians(lat0)))) * np.cos(a)
        ts = (t0 + timedelta(seconds=i)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        pts.append(f'<trkpt lat="{la:.6f}" lon="{lo:.6f}"><time>{ts}</time></trkpt>')
    return ('<?xml version="1.0"?><gpx version="1.1" creator="test" xmlns="http://www.topografix.com/GPX/1/1">'
            f"<trk><name>loop</name><type>{sport}</type><trkseg>{''.join(pts)}</trkseg></trk></gpx>")
