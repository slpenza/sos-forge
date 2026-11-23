"""Tests for asset validation/download and the HTTP retry and cache helpers."""
from __future__ import annotations

import hashlib
import io
import os
import time
import urllib.error

import pytest

from sosforge import fetch


def _asset(src, name="a") -> fetch.Asset:
    """Build an Asset pointing at a local file with its true size and digest. Helper; not itself tested."""
    data = src.read_bytes()
    return fetch.Asset(name, src.as_uri(), f"{name}.bin", len(data), hashlib.sha256(data).hexdigest())


def test_sha256_and_validate(tmp_path):
    """Checks streamed hashing and that missing, short and corrupt files fail validation while unpinned files pass. Covers sha256_file and validate_file; this test is not itself tested."""
    src = tmp_path / "src.bin"
    src.write_bytes(os.urandom(3 * fetch.CHUNK + 7))
    a = _asset(src)
    assert fetch.sha256_file(src) == hashlib.sha256(src.read_bytes()).hexdigest()
    assert fetch.validate_file(src, a) == (True, "ok")
    assert fetch.validate_file(tmp_path / "none", a)[1] == "missing"
    (tmp_path / "short").write_bytes(b"x")
    assert "size" in fetch.validate_file(tmp_path / "short", a)[1]
    (tmp_path / "bad").write_bytes(b"\0" * a.size)
    assert "sha256" in fetch.validate_file(tmp_path / "bad", a)[1]
    assert fetch.validate_file(tmp_path / "bad", fetch.Asset("u", "x", "bad"))[0]


def test_asset_download_skip_and_repair(tmp_path):
    """Checks first fetch downloads, a valid copy is reused, and a corrupted copy is re-downloaded. Covers ensure_asset and download over file:// URLs; HTTP resume is not tested."""
    src = tmp_path / "src.bin"
    src.write_bytes(os.urandom(2 * fetch.CHUNK + 3))
    a = _asset(src)
    p = fetch.ensure_asset(a, tmp_path / "assets")
    assert p.read_bytes() == src.read_bytes() and not list((tmp_path / "assets").glob("*.part"))
    m = p.stat().st_mtime_ns
    assert fetch.ensure_asset(a, tmp_path / "assets").stat().st_mtime_ns == m
    p.write_bytes(b"\0" * a.size)
    assert fetch.ensure_asset(a, tmp_path / "assets").read_bytes() == src.read_bytes()


def test_http_request_retries_then_succeeds(monkeypatch):
    """Checks 502 responses are retried with backoff and a later 200 body is returned, while 404 fails fast. Covers http_request with a stubbed opener; real network is not tested."""
    calls = []

    class Resp(io.BytesIO):
        """Minimal context-managed response. Helper; not itself tested."""

        def __enter__(self):
            """Return self for with-blocks. Helper; not itself tested."""
            return self

        def __exit__(self, *a):
            """Close nothing. Helper; not itself tested."""
            return False

    def fake(url, **kw):
        """Fail twice with 502 then succeed, or 404 for /missing. Helper; not itself tested."""
        calls.append(url)
        if "missing" in url:
            raise urllib.error.HTTPError(url, 404, "nf", {}, io.BytesIO(b"nope"))
        if len(calls) < 3:
            raise urllib.error.HTTPError(url, 502, "busy", {}, io.BytesIO(b"busy"))
        return Resp(b"ok")

    monkeypatch.setattr(fetch, "_urlopen", fake)
    monkeypatch.setattr(fetch.time, "sleep", lambda s: None)
    assert fetch.http_request("https://x/y", params={"a": 1}) == b"ok" and calls[0].endswith("?a=1")
    with pytest.raises(RuntimeError, match="HTTP 404"):
        fetch.http_request("https://x/missing")


def test_cached_call_honours_age_and_refresh(monkeypatch, tmp_path):
    """Checks the cache serves fresh entries, refetches stale ones and refetches on refresh. Covers cached_call and cache_root; this test is not itself tested."""
    n = []
    fetcher = lambda: (n.append(1), f"v{len(n)}".encode())[1]  # noqa: E731
    p = fetch.cached_call("t", {"k": 1}, fetcher, max_age_days=1)
    assert p.read_bytes() == b"v1" and str(p).startswith(str(fetch.cache_root()))
    assert fetch.cached_call("t", {"k": 1}, fetcher, max_age_days=1).read_bytes() == b"v1"
    old = time.time() - 3 * 86400
    os.utime(p, (old, old))
    assert fetch.cached_call("t", {"k": 1}, fetcher, max_age_days=1).read_bytes() == b"v2"
    assert fetch.cached_call("t", {"k": 1}, fetcher, max_age_days=1, refresh=True).read_bytes() == b"v3"
