"""The sos.stevenpenza.com site: gallery, simulator, help and Ask, assembled from site/ and published datasets.

`sos bundle <slug>` zips built dataset folders for a GitHub Release. `sos site` downloads those zips (or reads local
dist/ folders), makes browser previews (H.264 video and a poster), writes datasets.json, builds the help pages with
Ask & Answer's corpus.py, and copies Ask & Answer's panel files in. See design/v0/v0.2.0.md.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import zipfile
from pathlib import Path

from .encode import find_ffmpeg
from .fetch import cache_root, http_request, redirect_location
from .recipe import RecipeError, check_keys
from .timeline import parse_date

LOG = logging.getLogger("sosforge.site")
REPO_ROOT = Path(__file__).resolve().parents[1]
SITE_KEYS = {"domain", "repo", "ask_base", "ask_projects", "preview_width"}
DATASET_KEYS = {"slug", "release", "preview", "downloads", "dist", "dates", "lengths", "per", "label_style", "sources"}
STATIC_SKIP = (".test.mjs", "help-head.html", "help-bar.html", "site.toml")
PANEL_FILES = ("ask-client.js", "ask-panel.js", "ask-panel.css")
ASK_LOCK = Path(__file__).resolve().parents[1] / "ask.lock.json"   # the pinned Ask & Answer release (design/v0/v0.4.2.md)
HELP_KEYS = {"bar", "title", "groups", "landing", "layout", "ask"}


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
    check_keys("site.toml", raw, {"site", "datasets", "help"})
    site = raw.get("site", {})
    check_keys("site", site, SITE_KEYS)
    cfg = {"domain": site.get("domain", ""), "repo": site.get("repo", "slpenza/sos-forge"),
           "ask_base": site.get("ask_base"), "ask_projects": site.get("ask_projects", ["sos"]),
           "preview_width": int(site.get("preview_width", 2048)), "datasets": []}
    help_cfg = raw.get("help", {})
    check_keys("help", help_cfg, HELP_KEYS)
    cfg["help_frame"] = {k: help_cfg[k] for k in HELP_KEYS if k in help_cfg}
    missing = [t for g in cfg["help_frame"].get("groups", []) for t in g.get("topics", []) if not (REPO_ROOT / t).is_file()]
    if missing:
        raise RecipeError(f"[help] groups name topics that do not exist: {', '.join(missing)}")
    seen = set()
    for d in raw.get("datasets", []):
        check_keys("datasets", d, DATASET_KEYS)
        if "slug" not in d or d["slug"] in seen:
            raise RecipeError(f"[[datasets]] needs a unique slug (got {d.get('slug')!r})")
        if not d.get("dist") and not (d.get("release") and d.get("preview")):
            raise RecipeError(f"[[datasets]] {d['slug']}: give release + preview (a zip on that release) or dist (a folder)")
        lengths = d.get("lengths", [])
        label_style = d.get("label_style", False)
        if not isinstance(label_style, bool):
            raise RecipeError(f"[[datasets]] {d['slug']}: label_style is true or false")
        if lengths or label_style:
            if not (isinstance(lengths, list) and all(isinstance(s, int) and s > 0 for s in lengths)
                    and len(set(lengths)) == len(lengths)):
                raise RecipeError(f"[[datasets]] {d['slug']}: lengths must be distinct whole seconds, such as [20, 120]")
            if not d.get("release") or not d.get("downloads"):
                raise RecipeError(f"[[datasets]] {d['slug']}: lengths and label_style need a release and downloads to "
                                  "name them after")
            bad = [n for n in d["downloads"] if not n.startswith(f"{d['slug']}_")]
            if bad:
                raise RecipeError(f"[[datasets]] {d['slug']}: lengths and styles are named <slug>_<N>s_..., so every "
                                  f"download must start with {d['slug']}_ (not {', '.join(bad)})")
        per = d.get("per")
        if per is not None and not (isinstance(per, dict) and set(per) == {"unit", "count"} and isinstance(per["unit"], str)
                                    and per["unit"].strip() and isinstance(per["count"], (int, float)) and per["count"] > 0):
            raise RecipeError(f'[[datasets]] {d["slug"]}: per must be like {{ unit = "sport", count = 9 }}')
        sources = d.get("sources", [])
        if not (isinstance(sources, list) and all(isinstance(x, dict) and set(x) == {"label", "url"} and str(x["label"]).strip()
                                                  and str(x["url"]).startswith("https://") for x in sources)):
            raise RecipeError(f'[[datasets]] {d["slug"]}: sources must be like [{{ label = "...", url = "https://..." }}]')
        seen.add(d["slug"])
        cfg["datasets"].append({"per": per, "sources": sources, "slug": d["slug"], "release": d.get("release"), "preview": d.get("preview"),
                                "downloads": list(d.get("downloads", [])), "dist": d.get("dist"),
                                "dates": d.get("dates"), "lengths": sorted(lengths), "label_style": label_style})
    return cfg


def web_bundle(folder: Path, stem: str, dist: Path, width: int, ffmpeg: str) -> Path:
    """Zip a version's browser preview (preview.mp4, H.264 at most `width` wide) with its labels.txt and playlist.sos as dist/<stem>_web.zip, so the site can publish it without fetching the 4K movie. Tested by test_site.py::test_version_previews."""
    pl = parse_playlist((folder / "playlist.sos").read_text())
    zpath = Path(dist) / f"{stem}_web.zip"
    with tempfile.TemporaryDirectory() as tmp:
        make_preview(folder / str(pl.get("data", "")), Path(tmp), width, ffmpeg, poster=False, any_codec=True)
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_STORED) as z:
            z.write(Path(tmp) / "preview.mp4", f"{stem}_web/preview.mp4")
            for name in ("labels.txt", "playlist.sos"):
                if (folder / name).exists():
                    z.write(folder / name, f"{stem}_web/{name}")
    LOG.info("bundled %s (%.1f MB)", zpath.name, zpath.stat().st_size / 1e6)
    return zpath


def bundle(slug: str, dist: Path, web_width: int = 2048) -> list[Path]:
    """Zip every built folder for a slug (dist/<slug>, dist/<slug>_<codec>, and the length and style variants dist/<slug>_<N>s[_labels][_<codec>]) as dist/<folder>_<size>[_<codec>].zip for a release, plus each variant's browser preview as dist/<variant>_web.zip, made from its H.264 folder when there is one. Tested by test_site.py::test_bundle_and_folder_dataset, test_version_previews and test_build.py::test_length_variant_build."""
    dist = Path(dist)
    folders = sorted(p for p in dist.glob(f"{slug}*") if p.is_dir() and (p / "playlist.sos").exists()
                     and re.fullmatch(rf"{re.escape(slug)}(_\d+s)?(_(labels|panels))?(_(h264|mpeg4))?", p.name))
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
    stems = sorted({re.sub(r"_(h264|mpeg4)$", "", f.name) for f in folders} - {slug})
    ffmpeg = find_ffmpeg() if stems else ""
    for stem in stems:
        source = next(p for p in (dist / f"{stem}_h264", dist / stem, dist / f"{stem}_mpeg4") if p in folders)
        out.append(web_bundle(source, stem, dist, web_width, ffmpeg))
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
    """Extract a dataset zip safely (no absolute or parent paths) into a cleared folder, so nothing from an earlier zip is picked up, and return the folder holding playlist.sos. Tested by test_site.py::test_bundle_and_folder_dataset and test_version_previews."""
    if into.exists():
        shutil.rmtree(into)
    into.mkdir(parents=True)
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


def make_preview(src: Path, out_dir: Path, width: int, ffmpeg: str, *, poster: bool = True, any_codec: bool = False) -> dict:
    """Write a browser preview (H.264 MP4 or JPEG, at most `width` wide) and a poster JPEG unless told not to, returning their names and the source facts; any_codec lets a version's MPEG-4-only folder through. Tested by test_site.py::test_bundle_and_folder_dataset and test_version_previews."""
    out_dir.mkdir(parents=True, exist_ok=True)
    info = media_info(src, ffmpeg)
    w = min(width, info.get("width") or width)
    w -= w % 4
    scale = f"scale={w}:{w // 2}:flags=lanczos"
    is_video = src.suffix.lower() in (".mp4", ".m4v", ".mov", ".webm")
    run = lambda args: subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", *args], check=True)  # noqa: E731
    if is_video:
        if info.get("codec") not in (None, "h264") and not any_codec:
            raise RecipeError(f"{src.name} is {info.get('codec')}; previews need the H.264 folder (…_h264) because browsers can't play it")
        run(["-i", str(src), "-an", "-vf", f"{scale},format=yuv420p", "-c:v", "libx264", "-preset", "medium", "-crf", "24",
             "-profile:v", "main", "-movflags", "+faststart", str(out_dir / "preview.mp4")])
        if not poster:
            return {"preview": "preview.mp4", **info}
        at = max(0.0, info.get("duration_s", 0) / 3)
        run(["-ss", f"{at:.2f}", "-i", str(src), "-frames:v", "1", "-vf", "scale=1280:640:flags=lanczos", "-q:v", "3",
             str(out_dir / "poster.jpg")])
        return {"preview": "preview.mp4", "poster": "poster.jpg", **info}
    run(["-i", str(src), "-vf", scale, "-q:v", "2", str(out_dir / "preview.jpg")])
    run(["-i", str(src), "-vf", "scale=1280:640:flags=lanczos", "-q:v", "3", str(out_dir / "poster.jpg")])
    return {"preview": "preview.jpg", "poster": "poster.jpg", **info}


def date_label(start, end) -> str:
    """Return a short label for a dataset's span: whole years ("1820–2022") when it covers two years or more, otherwise day dates. Tested by test_site.py::test_recipe_dates."""
    a, b = parse_date(start), parse_date(end)
    if b.year - a.year >= 2:
        return f"{a.year}–{b.year}"
    day = lambda d: f"{d.day} {d:%b %Y}"
    return day(a) if a.date() == b.date() else f"{day(a)} – {day(b)}"


def recipe_span(slug: str, recipes: Path = REPO_ROOT / "recipes") -> tuple | None:
    """Return the (start, end) UTC datetimes of the recipe named by a dataset's slug, or None when there is no such recipe or span. Tested by test_site.py::test_recipe_dates against the repo's own recipes."""
    path = recipes / slug / "recipe.toml"
    if not path.is_file():
        return None
    anim = tomllib.loads(path.read_text()).get("animation", {})
    return (parse_date(anim["start"]), parse_date(anim["end"])) if anim.get("start") and anim.get("end") else None


def recipe_dates(slug: str, recipes: Path = REPO_ROOT / "recipes") -> str | None:
    """Return the date label of the recipe named by a dataset's slug, or None without a span. Tested by test_site.py::test_recipe_dates against the repo's own recipes."""
    span = recipe_span(slug, recipes)
    return date_label(*span) if span else None


def recipe_years(slug: str, recipes: Path = REPO_ROOT / "recipes") -> float | None:
    """Return how many years the recipe's span covers, the step a dated movie's pace is measured in, or None for spans under two years. Tested by test_site.py::test_lengths_and_download_checks."""
    span = recipe_span(slug, recipes)
    years = (span[1] - span[0]).days / 365.2425 if span else 0
    return round(years, 2) if years >= 2 else None


def version_stem(slug: str, seconds: int | None = None, style: str | None = None) -> str:
    """Return the folder name sos build gives a version of a dataset: getting_richer, getting_richer_120s, getting_richer_labels or getting_richer_120s_labels. Tested by test_site.py::test_lengths_and_download_checks."""
    return f"{slug}{f'_{seconds}s' if seconds else ''}{f'_{style}' if style else ''}"


def variant_name(name: str, slug: str, seconds: int | None = None, style: str | None = None) -> str:
    """Return a release asset's name for a length or text-style variant as sos bundle names it: getting_richer_4k.zip becomes getting_richer_120s_4k.zip, getting_richer_labels_4k.zip or getting_richer_120s_labels_4k.zip. Tested by test_site.py::test_lengths_and_download_checks."""
    return f"{version_stem(slug, seconds, style)}_{name[len(slug) + 1:]}"


def version_stems(d: dict) -> list[str]:
    """Return the folder names of a dataset's versions other than its own: each extra length, then the SOS-labels style at every length when the dataset offers it. Tested by test_site.py::test_lengths_and_download_checks."""
    stems = [version_stem(d["slug"], s) for s in d["lengths"]]
    if d["label_style"]:
        stems += [version_stem(d["slug"], s, "labels") for s in [None, *d["lengths"]]]
    return stems


def variant_names(d: dict) -> list[str]:
    """Return the release assets a dataset's other versions need: each one's MPEG-4 zip (the first download), since the standard length's H.264 zip already serves codec tests, and its browser preview zip (<stem>_web.zip). Tested by test_site.py::test_lengths_and_download_checks."""
    first = d["downloads"][0]
    names = [variant_name(first, d["slug"], s) for s in d["lengths"]]
    if d["label_style"]:
        names += [variant_name(first, d["slug"], s, "labels") for s in [None, *d["lengths"]]]
    return names + [f"{stem}_web.zip" for stem in version_stems(d)]


def length_entries(d: dict, repo: str, duration_s: float) -> list[dict]:
    """Return a dataset's lengths for datasets.json, shortest first: its own (the preview's, with every download) and each extra one (MPEG-4), each with its SOS-labels download when the dataset offers that style. Tested by test_site.py::test_lengths_and_download_checks."""
    url = lambda n: {"label": n, "url": f"https://github.com/{repo}/releases/download/{d['release']}/{n}"}
    first = d["downloads"][0]
    own = {"seconds": round(duration_s), "default": True, "downloads": [url(n) for n in d["downloads"]]}
    extra = [{"seconds": s, "default": False, "downloads": [url(variant_name(first, d["slug"], s))]} for s in d["lengths"]]
    entries = sorted([own, *extra], key=lambda x: x["seconds"])
    for e in entries:
        e["labels"] = (url(variant_name(first, d["slug"], None if e["default"] else e["seconds"], "labels"))
                       if d["label_style"] else None)
    return entries


def missing_downloads(repo: str, datasets: list[dict], listed: dict | None = None) -> list[str]:
    """Return every download (including length and text-style variants) that its release does not hold, listing each release once; a release that can't be listed is warned about and skipped. Tested by test_site.py::test_lengths_and_download_checks with stubbed HTTP."""
    listed = {} if listed is None else listed
    missing = []
    for d in datasets:
        if not d["release"] or not d["downloads"]:
            continue
        names = d["downloads"] + variant_names(d)
        if d["release"] not in listed:
            token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
            try:
                meta = json.loads(http_request(f"https://api.github.com/repos/{repo}/releases/tags/{d['release']}",
                                               headers={"Accept": "application/vnd.github+json",
                                                        **({"Authorization": f"Bearer {token}"} if token else {})},
                                               retries=3))
                listed[d["release"]] = {a.get("name") for a in meta.get("assets", [])}
            except RuntimeError as exc:
                LOG.warning("could not list release %s to check its downloads: %s", d["release"], exc)
                listed[d["release"]] = None
        if listed[d["release"]] is not None:
            missing += [f"{d['release']}/{n}" for n in names if n not in listed[d["release"]]]
    return missing


def dataset_entry(folder: Path, slug: str, out: Path, width: int, ffmpeg: str, downloads: list[dict],
                  dates: str | None = None) -> dict:
    """Turn one dataset folder into its published preview files, labels and datasets.json entry, carrying the date label it is given. Tested by test_site.py::test_bundle_and_folder_dataset and test_build_site_end_to_end."""
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
            "licences": lists["licences"], "downloads": downloads, "labels": labels, "dates": dates}


def version_preview(stem: str, slug: str, out: Path, *, dist: Path | None, repo: str, release: str, width: int,
                    ffmpeg: str, work: Path) -> dict:
    """Publish one version's browser preview and labels under datasets/<slug>/<tag>/, from dist (its _web.zip, else its built folder, H.264 preferred) or else its _web.zip on the release, returning their site paths. Tested by test_site.py::test_version_previews."""
    tag = stem[len(slug) + 1:]
    target = out / "datasets" / slug / tag
    target.mkdir(parents=True, exist_ok=True)
    web = Path(dist) / f"{stem}_web.zip" if dist else None
    built = next((p for p in (Path(dist) / f"{stem}_h264", Path(dist) / stem) if p.is_dir()), None) if dist else None
    if web and web.is_file():
        folder = unzip_folder(web, work / f"{stem}_web")
    elif built:
        folder = built
        pl = parse_playlist((folder / "playlist.sos").read_text())
        make_preview(folder / str(pl.get("data", "")), target, width, ffmpeg, poster=False, any_codec=True)
    else:
        folder = unzip_folder(release_asset(repo, release, f"{stem}_web.zip"), work / f"{stem}_web")
    if not (target / "preview.mp4").exists():
        if media_info(folder / "preview.mp4", ffmpeg).get("codec") != "h264":
            raise RecipeError(f"{stem}_web.zip: preview.mp4 is not H.264, so browsers can't play it")
        shutil.copy2(folder / "preview.mp4", target / "preview.mp4")
    labels = None
    if (folder / "labels.txt").exists():
        shutil.copy2(folder / "labels.txt", target / "labels.txt")
        labels = f"datasets/{slug}/{tag}/labels.txt"
    return {"video": f"datasets/{slug}/{tag}/preview.mp4", "labels": labels}


def ask_lock(path: Path | None = None) -> dict:
    """Read ask.lock.json, the pinned Ask & Answer release and the sha384 of each of its files, refusing one without corpus.py or the panel. Tested by test_site.py::test_fetch_ask_checks_the_lock."""
    path = path or ASK_LOCK
    if not path.is_file():
        raise RecipeError(f"{path} not found: pin a release with corpus.py --project . --lock <ask base>")
    lock = json.loads(path.read_text())
    files = lock.get("files", {}) if isinstance(lock, dict) else {}
    missing = [f for f in ("corpus.py", *PANEL_FILES) if f not in files]
    if not lock.get("release") or missing:
        raise RecipeError(f"{path} must name a release and pin {', '.join(missing) or 'its files'}")
    return lock


def sha384(data: bytes) -> str:
    """Return the Subresource Integrity form of a SHA-384 digest, as ask.lock.json records it. Tested by test_site.py::test_fetch_ask_checks_the_lock."""
    return "sha384-" + base64.b64encode(hashlib.sha384(data).digest()).decode()


def fetch_ask(ask_base: str, into: Path, lock_path: Path | None = None) -> Path:
    """Fetch the pinned Ask & Answer release into a folder: corpus.py checked here against ask.lock.json, then corpus.py --fetch, which checks itself and writes every other pinned file (the panel, docsite/) or none. Tested by test_site.py::test_fetch_ask_checks_the_lock against a stand-in API; the real one is exercised by sos site."""
    lock_path = lock_path or ASK_LOCK
    lock = ask_lock(lock_path)
    base = ask_base.rstrip("/")
    body = http_request(f"{base}/artifacts/{lock['release']}/corpus.py", retries=3)
    if sha384(body) != lock["files"]["corpus.py"]:
        raise RecipeError(f"corpus.py from {base} is not the one {lock_path.name} pins for {lock['release']}")
    if into.exists():
        shutil.rmtree(into)
    into.mkdir(parents=True)
    (into / "corpus.py").write_bytes(body)
    res = subprocess.run([sys.executable, "-I", str(into / "corpus.py"), "--project", str(lock_path.parent),
                          "--fetch", base, "--into", str(into)], capture_output=True, text=True)
    if res.returncode != 0:
        raise RecipeError(f"corpus.py could not fetch {lock['release']}:\n{res.stderr.strip() or res.stdout.strip()}")
    LOG.info("Ask & Answer %s fetched and checked against %s", lock["release"], lock_path.name)
    return into


UPLOAD_KEY_ENV = "ASK_UPLOAD_KEY"   # the upload key Ask & Answer issued for sos; a release secret, never in the repo


def upload_help(ask_base: str, lock_path: Path | None = None, key_env: str = UPLOAD_KEY_ENV) -> None:
    """Upload this project's help docs to Ask & Answer with the pinned release's own corpus.py (checked against ask.lock.json), reading the upload key from the environment variable key_env, so Help answers from what this release committed. Tested by test_site.py::test_upload_help_uses_the_pinned_corpus against a stand-in API; the real upload runs in tools/release.sh."""
    if not os.environ.get(key_env):
        raise RecipeError(f"${key_env} is not set: Help's docs reach Ask & Answer only through this upload "
                          "(design/v0/v0.4.10.md)")
    lock_path = lock_path or ASK_LOCK
    base = ask_base.rstrip("/")
    with tempfile.TemporaryDirectory() as tmp:
        corpus = fetch_ask(base, Path(tmp) / "ask", lock_path) / "corpus.py"
        res = subprocess.run([sys.executable, "-I", str(corpus), "--project", str(lock_path.parent), "--upload", base,
                              "--key-env", key_env], capture_output=True, text=True)
    if res.returncode != 0:
        raise RecipeError(f"corpus.py could not upload the help docs:\n{res.stderr.strip() or res.stdout.strip()}")
    LOG.info("help docs uploaded to %s: %s", base, (res.stdout.strip().splitlines() or [""])[-1])


def build_help(corpus: Path, out: Path, frame: dict, ask_base: str, key: str = "sos") -> None:
    """Write the help pages, with Ask & Answer's panel on their landing, from a temporary copy of the project whose askAnswer block gains the [help] frame (bar, title, groups, layout, ask), so package.json keeps only what the corpus needs. Tested by test_site.py::test_build_site_end_to_end with a stand-in corpus.py that records what it was given; the real tool is exercised by sos site."""
    pkg = json.loads((REPO_ROOT / "package.json").read_text())
    block = {**pkg["askAnswer"], **frame}
    with tempfile.TemporaryDirectory() as tmp:
        project = Path(tmp) / "project"
        project.mkdir()
        for rel in [*block.get("docs", []), *(block[k] for k in ("head", "bar", "landing") if block.get(k))]:
            src, dst = REPO_ROOT / rel, project / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            (shutil.copytree if src.is_dir() else shutil.copy2)(src, dst)
        (project / "package.json").write_text(json.dumps({**pkg, "askAnswer": block}, indent=1))
        res = subprocess.run([sys.executable, "-I", str(corpus), "--project", str(project), key,
                              "--docs-out", str(out / "help"), "--ask-base", ask_base.rstrip("/")],
                             capture_output=True, text=True)
    if res.returncode != 0:
        raise RecipeError(f"corpus.py could not build the help:\n{res.stderr.strip() or res.stdout.strip()}")
    LOG.info("help pages written (%d files)", sum(1 for _ in (out / "help").rglob("*") if _.is_file()))


HELP_HREF = re.compile(r'href="/?help/"')


def point_help_at_docs(out: Path, repo: str) -> int:
    """Rewrite the site's links to /help/ to the docs on GitHub when the build has no help pages (no Ask & Answer base), returning how many links changed. Tested by test_site.py::test_build_site_end_to_end's build without an Ask & Answer base."""
    url = f"https://github.com/{repo}/blob/main/docs/getting-started.md"
    changed = 0
    for page in out.glob("*.html"):
        text, n = HELP_HREF.subn(f'href="{url}"', page.read_text())
        if n:
            page.write_text(text)
            changed += n
    return changed


def build_site(config_path: Path, out: Path, *, dist: Path | None = None, ask_base: str | None = None,
               check_downloads: bool = False, ask_lock_path: Path | None = None) -> dict:
    """Assemble the whole site into `out` and return the manifest written as datasets.json; ask_base defaults to site.toml's, and an empty one builds without Ask & Answer. Tested by test_site.py::test_build_site_end_to_end."""
    cfg = load_config(config_path)
    ask_base = cfg["ask_base"] if ask_base is None else ask_base
    if check_downloads:
        missing = missing_downloads(cfg["repo"], cfg["datasets"])
        if missing:
            raise RecipeError("site.toml links downloads its releases don't hold (upload them with "
                              f"gh release upload): {', '.join(missing)}")
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
        entry = dataset_entry(folder, d["slug"], out, cfg["preview_width"], ffmpeg, downloads,
                              d.get("dates") or recipe_dates(d["slug"]))
        entry["lengths"] = length_entries(d, cfg["repo"], entry["duration_s"]) if entry["kind"] == "movie" and d["release"] else []
        for e in entry["lengths"]:
            e["previews"] = {"painted": {"video": entry["preview"], "labels": entry["labels"]}} if e["default"] else {}
            for style in ("painted", "labels") if d["label_style"] else ("painted",):
                if style not in e["previews"]:
                    stem = version_stem(d["slug"], None if e["default"] else e["seconds"], "labels" if style == "labels" else None)
                    e["previews"][style] = version_preview(stem, d["slug"], out, dist=dist, repo=cfg["repo"], release=d["release"],
                                                           width=cfg["preview_width"], ffmpeg=ffmpeg, work=work)
        entry["sources"] = d["sources"]
        years = recipe_years(d["slug"])
        entry["per"] = d.get("per") or ({"unit": "year", "count": years} if years else None)
        entries.append(entry)
        LOG.info("dataset %s ready", d["slug"])
    manifest = {"datasets": entries}
    (out / "datasets.json").write_text(json.dumps(manifest, indent=1))
    ask_cfg: dict = {"askProjects": cfg["ask_projects"]}
    if ask_base:
        files = fetch_ask(ask_base, work / "ask", ask_lock_path)
        build_help(files / "corpus.py", out, cfg["help_frame"], ask_base)
        (out / "ask").mkdir(exist_ok=True)
        for f in PANEL_FILES:
            shutil.copy2(files / f, out / "ask" / f)
        ask_cfg["askBase"] = ask_base.rstrip("/")
    else:
        n = point_help_at_docs(out, cfg["repo"])
        LOG.warning("no Ask & Answer base ($SOSFORGE_ASK_BASE): building without the help pages or the ask box; "
                    "%d Help links point at the docs on GitHub", n)
    (out / "config.json").write_text(json.dumps(ask_cfg, indent=1))
    LOG.info("site built in %s: %d datasets", out, len(entries))
    return manifest
