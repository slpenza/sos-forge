"""The sos.stevenpenza.com site: gallery, simulator, help and Ask, assembled from site/ and published datasets.

`sos bundle <slug>` zips built dataset folders for a GitHub Release. `sos site` downloads those zips (or reads local
dist/ folders), makes browser previews (H.264 video and a poster), writes datasets.json, builds the help pages with
Ask & Answer's corpus.py, and copies Ask & Answer's panel files in. See design/v0/v0.2.0.md.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path

from .encode import find_ffmpeg
from .fetch import cache_root, http_request, redirect_location
from .recipe import RecipeError, check_keys

LOG = logging.getLogger("sosforge.site")
REPO_ROOT = Path(__file__).resolve().parents[1]
SITE_KEYS = {"domain", "repo", "ask_release", "ask_projects", "preview_width"}
DATASET_KEYS = {"slug", "release", "preview", "downloads", "dist"}
STATIC_SKIP = (".test.mjs", "help-head.html", "help-bar.html", "site.toml")
PANEL_FILES = ("ask-client.js", "ask-panel.js", "ask-panel.css")


def parse_playlist(text: str) -> dict:
    """Parse playlist.sos text into keys with numeric values as numbers and a {{ }} description joined to one line. Tested by test_site.py::test_parse_playlist_and_readme."""
    out: dict = {}
    lines = text.replace("\r\n", "\n").split("\n")
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        i += 1
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip().lower(), value.strip()
        if value.startswith("{{"):
            body = value[2:]
            while "}}" not in body and i < len(lines):
                body += " " + lines[i].strip()
                i += 1
            out[key] = " ".join(body.split("}}")[0].split())
            continue
        out[key] = float(value) if re.fullmatch(r"-?\d+\.\d+", value) else int(value) if re.fullmatch(r"-?\d+", value) else value
    return out


def readme_lists(text: str) -> dict[str, list[str]]:
    """Return the bullet lists under the Credits and Licences headings of a dataset README. Tested by test_site.py::test_parse_playlist_and_readme."""
    out: dict[str, list[str]] = {"credits": [], "licences": []}
    current = None
    for line in text.splitlines():
        if line.startswith("## "):
            title = line[3:].strip().lower()
            current = "credits" if title == "credits" else "licences" if title in ("licences", "licenses") else None
        elif current and line.startswith("- "):
            item = line[2:].strip()
            if item and not item.startswith("("):
                out[current].append(item)
    return out


def load_config(path: Path) -> dict:
    """Load and validate site/site.toml, filling defaults and checking every dataset entry. Tested by test_site.py::test_config_validation."""
    raw = tomllib.loads(Path(path).read_text())
    check_keys("site.toml", raw, {"site", "datasets"})
    site = raw.get("site", {})
    check_keys("site", site, SITE_KEYS)
    cfg = {"domain": site.get("domain", ""), "repo": site.get("repo", "slpenza/sos-forge"),
           "ask_release": site.get("ask_release"), "ask_projects": site.get("ask_projects", ["sos"]),
           "preview_width": int(site.get("preview_width", 2048)), "datasets": []}
    seen = set()
    for d in raw.get("datasets", []):
        check_keys("datasets", d, DATASET_KEYS)
        if "slug" not in d or d["slug"] in seen:
            raise RecipeError(f"[[datasets]] needs a unique slug (got {d.get('slug')!r})")
        if not d.get("dist") and not (d.get("release") and d.get("preview")):
            raise RecipeError(f"[[datasets]] {d['slug']}: give release + preview (a zip on that release) or dist (a folder)")
        seen.add(d["slug"])
        cfg["datasets"].append({"slug": d["slug"], "release": d.get("release"), "preview": d.get("preview"),
                                "downloads": list(d.get("downloads", [])), "dist": d.get("dist")})
    return cfg


def bundle(slug: str, dist: Path) -> list[Path]:
    """Zip every built folder for a slug (dist/<slug>, dist/<slug>_<codec>) as dist/<folder>_<size>.zip for a release. Tested by test_site.py::test_bundle_and_folder_dataset."""
    dist = Path(dist)
    folders = sorted(p for p in dist.glob(f"{slug}*") if p.is_dir() and (p / "playlist.sos").exists()
                     and (p.name == slug or re.fullmatch(rf"{re.escape(slug)}_(h264|mpeg4)", p.name)))
    if not folders:
        raise RecipeError(f"no built folders for {slug!r} in {dist} (run sos build {slug} first)")
    out = []
    for folder in folders:
        data = parse_playlist((folder / "playlist.sos").read_text()).get("data", "")
        size = re.search(r"_(\d+k|\d+x\d+)(?:_\w+)?\.\w+$", data)
        name = folder.name + (f"_{size.group(1)}" if size else "")
        if folder.name.endswith(("_h264", "_mpeg4")) and size:
            base, codec = folder.name.rsplit("_", 1)
            name = f"{base}_{size.group(1)}_{codec}"
        zpath = dist / f"{name}.zip"
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_STORED) as z:
            for f in sorted(folder.iterdir()):
                if f.is_file():
                    z.write(f, f"{folder.name}/{f.name}")
        LOG.info("bundled %s (%.1f MB)", zpath.name, zpath.stat().st_size / 1e6)
        out.append(zpath)
    return out


def release_asset(repo: str, tag: str, name: str) -> Path:
    """Download one release asset to the shared cache (once) and return its path; with $GH_TOKEN or $GITHUB_TOKEN it goes through the asset API, which private repos need, and the token never reaches the storage host it redirects to. Tested by test_site.py::test_release_assets_are_fetched_and_cached with stubbed HTTP."""
    dest = cache_root() / "releases" / repo.replace("/", "_") / tag / name
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    auth = {"Authorization": f"Bearer {token}"} if token else {}
    meta = json.loads(http_request(f"https://api.github.com/repos/{repo}/releases/tags/{tag}",
                                   headers={"Accept": "application/vnd.github+json", **auth}, retries=3))
    asset = next((a for a in meta.get("assets", []) if a.get("name") == name), None)
    if asset is None:
        have = ", ".join(a.get("name", "?") for a in meta.get("assets", [])) or "none"
        raise RecipeError(f"release {tag} of {repo} has no asset {name!r} (has: {have}); upload it with sos bundle + gh release upload {tag}")
    if token:
        api = {"Accept": "application/octet-stream", **auth}
        signed = redirect_location(asset["url"], api)
        body = http_request(signed, retries=3, timeout=900) if signed else http_request(asset["url"], headers=api,
                                                                                        retries=3, timeout=900)
    else:
        body = http_request(asset["browser_download_url"], retries=3, timeout=900)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    tmp.write_bytes(body)
    tmp.replace(dest)
    LOG.info("downloaded %s from %s (%.1f MB)", name, tag, dest.stat().st_size / 1e6)
    return dest


def unzip_folder(zpath: Path, into: Path) -> Path:
    """Extract a dataset zip safely (no absolute or parent paths) and return the folder holding playlist.sos. Tested by test_site.py::test_bundle_and_folder_dataset."""
    into.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zpath) as z:
        for member in z.namelist():
            if member.startswith("/") or ".." in Path(member).parts:
                raise RecipeError(f"{zpath.name} contains an unsafe path: {member}")
        z.extractall(into)
    found = sorted(into.rglob("playlist.sos"))
    if not found:
        raise RecipeError(f"{zpath.name} has no playlist.sos")
    return found[0].parent


def media_info(path: Path, ffmpeg: str) -> dict:
    """Return width, height, duration and frame rate for a video or image by reading ffmpeg's header report. Tested by test_site.py::test_bundle_and_folder_dataset."""
    err = subprocess.run([ffmpeg, "-hide_banner", "-i", str(path)], capture_output=True, text=True).stderr
    info: dict = {}
    m = re.search(r"Video: (\w+).*?, (\d{2,5})x(\d{2,5})", err)
    if m:
        info.update(codec=m.group(1), width=int(m.group(2)), height=int(m.group(3)))
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", err)
    if m:
        info["duration_s"] = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    m = re.search(r", ([\d.]+) fps", err)
    if m:
        info["fps"] = float(m.group(1))
    return info


def make_preview(src: Path, out_dir: Path, width: int, ffmpeg: str) -> dict:
    """Write a browser preview (H.264 MP4 or JPEG, at most `width` wide) and a poster JPEG, returning their names and the source facts. Tested by test_site.py::test_bundle_and_folder_dataset."""
    out_dir.mkdir(parents=True, exist_ok=True)
    info = media_info(src, ffmpeg)
    w = min(width, info.get("width") or width)
    w -= w % 4
    scale = f"scale={w}:{w // 2}:flags=lanczos"
    is_video = src.suffix.lower() in (".mp4", ".m4v", ".mov", ".webm")
    run = lambda args: subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", *args], check=True)  # noqa: E731
    if is_video:
        if info.get("codec") not in (None, "h264"):
            raise RecipeError(f"{src.name} is {info.get('codec')}; previews need the H.264 folder (…_h264) because browsers can't play it")
        run(["-i", str(src), "-an", "-vf", f"{scale},format=yuv420p", "-c:v", "libx264", "-preset", "medium", "-crf", "24",
             "-profile:v", "main", "-movflags", "+faststart", str(out_dir / "preview.mp4")])
        at = max(0.0, info.get("duration_s", 0) / 3)
        run(["-ss", f"{at:.2f}", "-i", str(src), "-frames:v", "1", "-vf", "scale=1280:640:flags=lanczos", "-q:v", "3",
             str(out_dir / "poster.jpg")])
        return {"preview": "preview.mp4", "poster": "poster.jpg", **info}
    run(["-i", str(src), "-vf", scale, "-q:v", "2", str(out_dir / "preview.jpg")])
    run(["-i", str(src), "-vf", "scale=1280:640:flags=lanczos", "-q:v", "3", str(out_dir / "poster.jpg")])
    return {"preview": "preview.jpg", "poster": "poster.jpg", **info}


def dataset_entry(folder: Path, slug: str, out: Path, width: int, ffmpeg: str, downloads: list[dict]) -> dict:
    """Turn one dataset folder into its published preview files, labels and datasets.json entry. Tested by test_site.py::test_bundle_and_folder_dataset."""
    playlist_text = (folder / "playlist.sos").read_text()
    pl = parse_playlist(playlist_text)
    media = folder / str(pl.get("data", ""))
    if not media.is_file():
        raise RecipeError(f"{folder}: playlist names {pl.get('data')!r}, which is not in the folder")
    target = out / "datasets" / slug
    facts = make_preview(media, target, width, ffmpeg)
    (target / "playlist.sos").write_text(playlist_text)
    lists = readme_lists((folder / "README.md").read_text()) if (folder / "README.md").exists() else {"credits": [], "licences": []}
    kind = "movie" if facts["preview"].endswith(".mp4") else "texture"
    labels = None
    if kind == "movie" and (folder / "labels.txt").exists():
        shutil.copy2(folder / "labels.txt", target / "labels.txt")
        labels = f"datasets/{slug}/labels.txt"
    name = re.sub(r"\s*\((H\.264|MPEG-4 Part 2)\)$", "", str(pl.get("name", slug)))
    return {"slug": slug, "name": name, "kind": kind, "playlist": {**pl, "name": name},
            "preview": f"datasets/{slug}/{facts['preview']}", "poster": f"datasets/{slug}/{facts['poster']}",
            "source_fps": facts.get("fps", pl.get("fps", 30)), "duration_s": facts.get("duration_s", 0),
            "width": facts.get("width"), "height": facts.get("height"), "credits": lists["credits"],
            "licences": lists["licences"], "downloads": downloads, "labels": labels}


def build_help(ask_checkout: Path, out: Path, key: str = "sos") -> None:
    """Write the help pages with Ask & Answer's corpus.py from the pinned checkout, reading this repo's package.json. Tested by test_site.py::test_build_site_end_to_end with a stand-in corpus.py; the real tool is exercised by sos site."""
    corpus = ask_checkout / "tools" / "corpus.py"
    if not corpus.exists():
        raise RecipeError(f"{corpus} not found: point --ask-answer (or $SOSFORGE_ASK_ANSWER) at an ask-answer checkout")
    res = subprocess.run([sys.executable, str(corpus), "--project", str(REPO_ROOT), key, "--docs-out", str(out / "help")],
                         capture_output=True, text=True)
    if res.returncode != 0:
        raise RecipeError(f"corpus.py could not build the help:\n{res.stderr.strip() or res.stdout.strip()}")
    LOG.info("help pages written (%d files)", sum(1 for _ in (out / "help").rglob("*") if _.is_file()))


def ask_checkout_release(ask_checkout: Path) -> str | None:
    """Return the tag the ask-answer checkout sits exactly on, or None when it is between tags. Tested indirectly by test_site.py::test_build_site_end_to_end."""
    res = subprocess.run(["git", "-C", str(ask_checkout), "describe", "--tags", "--exact-match"], capture_output=True, text=True)
    return res.stdout.strip() or None


def build_site(config_path: Path, out: Path, *, dist: Path | None = None, ask_checkout: Path | None = None,
               ask_base: str | None = None, strict_ask: bool = False) -> dict:
    """Assemble the whole site into `out` and return the manifest written as datasets.json. Tested by test_site.py::test_build_site_end_to_end."""
    cfg = load_config(config_path)
    out = Path(out)
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(REPO_ROOT / "site", out, ignore=lambda d, names: [n for n in names if n.endswith(STATIC_SKIP)])
    ffmpeg = find_ffmpeg()
    work = cache_root() / "site-work"
    entries = []
    for d in cfg["datasets"]:
        downloads = [{"label": n, "url": f"https://github.com/{cfg['repo']}/releases/download/{d['release']}/{n}"}
                     for n in d["downloads"]] if d["release"] else []
        local = [Path(dist) / n for n in ([d["dist"]] if d["dist"] else [f"{d['slug']}_h264", d["slug"]])] if dist else []
        if any(p.is_dir() for p in local):
            folder = next(p for p in local if p.is_dir())
        elif d["dist"]:
            folder = Path(d["dist"])
        else:
            folder = unzip_folder(release_asset(cfg["repo"], d["release"], d["preview"]), work / d["slug"])
        entries.append(dataset_entry(folder, d["slug"], out, cfg["preview_width"], ffmpeg, downloads))
        LOG.info("dataset %s ready", d["slug"])
    manifest = {"datasets": entries}
    (out / "datasets.json").write_text(json.dumps(manifest, indent=1))
    ask_cfg: dict = {"askProjects": cfg["ask_projects"]}
    if ask_checkout:
        have = ask_checkout_release(ask_checkout)
        if have != cfg["ask_release"]:
            msg = f"ask-answer checkout is at {have or 'no tag'}, site.toml pins {cfg['ask_release']}"
            if strict_ask:
                raise RecipeError(msg)
            LOG.warning(msg)
        build_help(ask_checkout, out)
        (out / "ask").mkdir(exist_ok=True)
        for f in PANEL_FILES:
            shutil.copy2(ask_checkout / "client" / f, out / "ask" / f)
        if ask_base:
            ask_cfg["askBase"] = ask_base.rstrip("/")
    else:
        LOG.warning("no ask-answer checkout: building without the help pages and the Ask panel")
    (out / "config.json").write_text(json.dumps(ask_cfg, indent=1))
    LOG.info("site built in %s: %d datasets", out, len(entries))
    return manifest
