"""Downloads and API calls: validated asset files plus polite, cached HTTP for data sources.

Everything lands under the cache root ($SOSFORGE_CACHE or ~/.cache/sosforge) so recipes share downloads.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .meta import USER_AGENT

LOG = logging.getLogger("sosforge.fetch")
CHUNK = 1 << 20
RETRY_STATUS = {429, 500, 502, 503, 504}


def cache_root() -> Path:
    """Return the shared cache directory, honouring $SOSFORGE_CACHE. Tested by test_fetch.py::test_cached_call_honours_age_and_refresh."""
    root = Path(os.environ.get("SOSFORGE_CACHE") or Path.home() / ".cache" / "sosforge")
    root.mkdir(parents=True, exist_ok=True)
    return root


@dataclass(frozen=True)
class Asset:
    """A downloadable file with optional exact byte size and pinned SHA-256. Tested by test_fetch.py::test_asset_download_skip_and_repair."""

    name: str
    url: str
    filename: str
    size: int | None = None
    sha256: str | None = None
    note: str = ""

    @classmethod
    def from_spec(cls, name: str, spec: dict) -> "Asset":
        """Build an Asset from a recipe [assets.<name>] table, defaulting the filename to the URL's basename. Tested by test_recipe.py::test_load_recipe_defaults_and_assets."""
        url = spec["url"]
        filename = spec.get("filename") or Path(urllib.parse.urlparse(url).path).name
        return cls(name, url, filename, spec.get("size"), spec.get("sha256"), spec.get("note", ""))


def sha256_file(path: Path) -> str:
    """Return the hex SHA-256 of a file streamed in 1 MiB chunks. Tested by test_fetch.py::test_sha256_and_validate."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for block in iter(lambda: fh.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_file(path: Path, asset: Asset) -> tuple[bool, str]:
    """Check a local file exists and matches the asset's size and SHA-256 when those are pinned. Tested by test_fetch.py::test_sha256_and_validate."""
    path = Path(path)
    if not path.is_file():
        return False, "missing"
    if asset.size is not None and path.stat().st_size != asset.size:
        return False, f"size {path.stat().st_size} != expected {asset.size}"
    if asset.sha256:
        digest = sha256_file(path)
        if digest != asset.sha256:
            return False, f"sha256 {digest[:12]}... != pinned {asset.sha256[:12]}..."
    return True, "ok"


def _urlopen(url: str, *, data: bytes | None = None, headers: dict | None = None, offset: int = 0,
             timeout: float = 120.0):
    """Open a URL with the sos-forge User-Agent, optional POST body, headers and resume offset. Tested indirectly through file:// URLs; HTTP-only branches are exercised in live builds."""
    req = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT, **(headers or {})})
    if offset and url.startswith("http"):
        req.add_header("Range", f"bytes={offset}-")
    return urllib.request.urlopen(req, timeout=timeout)


def download(asset: Asset, dest: Path, retries: int = 3) -> Path:
    """Stream an asset to dest through a .part file with progress logging, resume and retries, then validate and rename. Tested by test_fetch.py::test_asset_download_skip_and_repair with file:// URLs; HTTP resume is not tested."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    for attempt in range(1, retries + 1):
        offset = part.stat().st_size if part.exists() else 0
        try:
            with _urlopen(asset.url, offset=offset) as resp:
                if offset and getattr(resp, "status", 200) != 206:
                    offset = 0
                total = asset.size or int(resp.headers.get("Content-Length") or 0) + offset
                done, t0, next_pct = offset, time.monotonic(), 0
                LOG.info("GET %s (%.1f MB)", asset.url, total / 1e6 if total else float("nan"))
                with part.open("ab" if offset else "wb") as out:
                    for block in iter(lambda: resp.read(CHUNK), b""):
                        out.write(block)
                        done += len(block)
                        pct = int(100 * done / total) if total else 0
                        if total and pct >= next_pct:
                            rate = (done - offset) / max(time.monotonic() - t0, 1e-6) / 1e6
                            LOG.info("  %-32s %3d%%  %7.1f MB  %5.1f MB/s", asset.filename, pct, done / 1e6, rate)
                            next_pct = pct - pct % 25 + 25
            ok, reason = validate_file(part, asset)
            if not ok:
                part.unlink(missing_ok=True)
                raise ValueError(f"{asset.filename} failed validation: {reason}")
            part.replace(dest)
            return dest
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
            if attempt == retries:
                raise RuntimeError(f"download of {asset.url} failed after {retries} attempts: {exc}") from exc
            LOG.warning("  attempt %d/%d failed (%s); retrying in %ds", attempt, retries, exc, 2 ** attempt)
            time.sleep(2 ** attempt)
    raise RuntimeError("unreachable")


def ensure_asset(asset: Asset, directory: Path | None = None, force: bool = False) -> Path:
    """Return a validated local copy of an asset, downloading only when missing, corrupt or forced. Tested by test_fetch.py::test_asset_download_skip_and_repair."""
    dest = Path(directory or cache_root() / "assets") / asset.filename
    ok, reason = (False, "forced") if force else validate_file(dest, asset)
    if ok:
        LOG.info("%-32s cached and valid", asset.filename)
        return dest
    LOG.info("%-32s %s - downloading", asset.filename, reason)
    return download(asset, dest)


def http_request(url: str, *, params: dict | None = None, data: bytes | None = None, headers: dict | None = None,
                 timeout: float = 300.0, retries: int = 6, backoff_s: float = 5.0) -> bytes:
    """Perform a GET or POST with exponential backoff on 429 and 5xx responses and return the body bytes. Tested by test_fetch.py::test_http_request_retries_then_succeeds with a stubbed opener."""
    if params:
        url = f"{url}{'&' if '?' in url else '?'}{urllib.parse.urlencode(params)}"
    delay = backoff_s
    for attempt in range(1, retries + 1):
        try:
            with _urlopen(url, data=data, headers=headers, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code not in RETRY_STATUS or attempt == retries:
                body = exc.read()[:300].decode("utf-8", "replace") if hasattr(exc, "read") else ""
                raise RuntimeError(f"HTTP {exc.code} from {url.split('?')[0]}: {body.strip()}") from exc
            wait = float(exc.headers.get("Retry-After") or delay) if exc.headers else delay
            LOG.warning("  HTTP %d from %s; retry %d/%d in %.0fs", exc.code, url.split("?")[0], attempt, retries, wait)
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            if attempt == retries:
                raise RuntimeError(f"request to {url.split('?')[0]} failed: {exc}") from exc
            wait = delay
            LOG.warning("  %s; retry %d/%d in %.0fs", exc, attempt, retries, wait)
        time.sleep(wait)
        delay *= 2
    raise RuntimeError("unreachable")


def cached_call(namespace: str, key: dict, fetch, *, suffix: str = ".bin", max_age_days: float = 30.0,
                refresh: bool = False) -> Path:
    """Return a cache file for a JSON-serialisable key, calling fetch() to refill it when absent, stale or refreshed. Tested by test_fetch.py::test_cached_call_honours_age_and_refresh."""
    digest = hashlib.sha1(json.dumps(key, sort_keys=True).encode()).hexdigest()[:16]
    path = cache_root() / "sources" / namespace / f"{digest}{suffix}"
    fresh = path.exists() and (time.time() - path.stat().st_mtime) < max_age_days * 86400
    if fresh and not refresh:
        LOG.info("%s cache hit %s (%.1f days old)", namespace, path.name, (time.time() - path.stat().st_mtime) / 86400)
        return path
    body = fetch()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(body)
    tmp.replace(path)
    path.with_name(path.name + ".key.json").write_text(json.dumps(key, sort_keys=True, indent=1))
    return path
