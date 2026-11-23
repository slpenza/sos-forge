"""SOS deployment folder: the data file, playlist.sos and a README with credits, licence, checks and install steps."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from .fetch import sha256_file
from .meta import REPO_URL
from .recipe import Recipe

SOS_SITE_CUSTOM = "/shared/sos/media/site-custom"
CODEC_NAMES = {"mpeg4": "MPEG-4 Part 2", "h264": "H.264"}


def playlist_text(recipe: Recipe, data_name: str, credits: list[str], variant: str | None = None,
                  labels: bool = False) -> str:
    """Return playlist.sos with name (suffixed for codec variants), a single-line {{ }} description with credits, data, fps and labels for movies, and [playlist] extras. Tested by test_package.py::test_playlist_and_readme."""
    desc = " ".join(recipe.dataset["description"].split())
    if credits:
        desc = f"{desc} Credits: {'; '.join(credits)}.".strip()
    name = recipe.dataset["name"] + (f" ({CODEC_NAMES[variant]})" if variant else "")
    lines = [f"name = {name}", f"description = {{{{ {desc} }}}}", f"data = {data_name}"]
    if recipe.is_movie and "fps" not in recipe.playlist:
        lines.append(f"fps = {recipe.animation['fps']}")
    if labels:
        visible = "true" if recipe.hud["mode"] == "labels" else "false"
        lines += ["label = labels.txt", f"labelvisible = {visible}"]
    lines += [f"{k} = {v:g}" if isinstance(v, float) else f"{k} = {v}" for k, v in recipe.playlist.items()]
    return "\n".join(lines) + "\n"


def _table(checks: list[dict]) -> str:
    """Format check results as a Markdown table. Tested by test_package.py::test_playlist_and_readme."""
    rows = ["| Check | Result | Measurement |", "|---|---|---|"]
    return "\n".join(rows + [f"| {c['name']} | {'PASS' if c['passed'] else 'FAIL'} | {c['detail']} |" for c in checks])


def readme_text(recipe: Recipe, data_name: str, sha: str, summary: dict, credits: list[str], licenses: list[str],
                folder: str, variant: str | None = None, primary: tuple[str, str] | None = None) -> str:
    """Return the deployment README with contents, encoding notes, install steps, credits, licences, settings and checks. Tested by test_package.py::test_playlist_and_readme."""
    target = f"{SOS_SITE_CUSTOM}/{folder}"
    codec = (summary.get("video") or {}).get("codec")
    if variant and primary:
        enc = (f"This folder is the **{CODEC_NAMES[variant]} variant** of the same frames, built to test which codec "
               f"your SOS plays best. The primary dataset is `{primary[1]}/` ({CODEC_NAMES[primary[0]]}). Deploy both "
               f"and compare on the sphere; they show up as separate playlist entries.")
    elif codec:
        enc = (f"Encoded as **{CODEC_NAMES.get(codec, codec)}**, yuv420p, no B-frames, a keyframe every second. "
               f"NOAA's content guide recommends MPEG-4 Part 2 at 25 Mbps or more and warns that H.264 can cause "
               f"errors in some SOS software versions.")
    else:
        enc = "Still image (texture); no video encoding."
    kind = "movie" if recipe.is_movie else "still image (texture)"
    media = summary.get("video") or summary.get("image") or {}
    lic = "\n".join(f"- {x}" for x in licenses) or "- See credits."
    cred = "\n".join(f"- {c}" for c in credits) or "- (none declared)"
    return f"""# {recipe.dataset['name']}

{recipe.dataset['description']}

Science On a Sphere {kind}, {summary['width']}x{summary['height']} equirectangular.
Built with sos-forge ({REPO_URL}) from recipe `{recipe.slug}`.

{enc}

## Contents

| File | Bytes | SHA-256 |
|---|---|---|
| `{data_name}` | {media.get('bytes', 0)} | `{sha}` |
| `playlist.sos` | | |
| `labels.txt` (movies) | | |
| `README.md` | | |

## Deploy to `{target}/`

```bash
rsync -av --chmod=Da+rx,Fa+r {folder}/ sos@<sos-primary>:{target}/
ssh sos@<sos-primary> sha256sum {target}/{data_name}   # must match the table above
```

Then refresh the library (SOS Stream GUI: Library > Update Library) and add the dataset to a presentation; it appears
under Site-Custom. `playlist.sos` refers to `{data_name}` relative to its own folder.

## Credits

{cred}

## Licences

{lic}

## Settings

```json
{json.dumps(summary['settings'], indent=2, default=str)}
```

## Verification (this build)

{_table(summary['checks'])}
"""


def build_package(recipe: Recipe, data_file: Path, dist_dir: Path, summary: dict, credits: list[str],
                  licenses: list[str], variant: str | None = None, primary: tuple[str, str] | None = None,
                  labels: Path | None = None) -> dict:
    """Copy the data file (and labels.txt for movies) into the dist folder and write playlist.sos and README.md beside it, marking codec variants. Tested by test_build.py::test_texture_build_end_to_end and test_movie_build_end_to_end."""
    dist_dir.mkdir(parents=True, exist_ok=True)
    target = dist_dir / data_file.name
    if target.resolve() != data_file.resolve():
        shutil.copy2(data_file, target)
    sha = sha256_file(target)
    playlist = dist_dir / "playlist.sos"
    if labels is not None and Path(labels).exists():
        shutil.copy2(labels, dist_dir / "labels.txt")
    playlist.write_text(playlist_text(recipe, target.name, credits, variant, labels=labels is not None))
    readme = dist_dir / "README.md"
    readme.write_text(readme_text(recipe, target.name, sha, summary, credits, licenses, dist_dir.name, variant, primary))
    return {"data": target, "playlist": playlist, "readme": readme, "sha256": sha}
