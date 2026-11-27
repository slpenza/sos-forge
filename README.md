# sos-forge

Build [NOAA Science On a Sphere](https://sos.noaa.gov) datasets from open data with a short recipe file.

A recipe says *what* to show (a basemap, points, GPS tracks, a sunlit planet), *how* to time it, and what the
presenter panel says. `sos build` does everything else:

- fetches and caches the data
- renders seamless 2:1 equirectangular frames in parallel
- encodes an SOS-compatible MP4 or still image
- verifies the result: alignment, the ±180° seam, frame count, B-frames and colour tags
- writes a deployable folder with `playlist.sos` and a README

```bash
uv venv --python 3.13 .venv && uv pip install --python .venv/bin/python -e ".[dev]"
source .venv/bin/activate

sos list                                   # bundled + local recipes
sos new "Coffee Shops"                     # scaffold recipes/coffee_shops/ with a sample CSV
sos build coffee_shops --preview           # 1024x512 in seconds -> dist/coffee_shops_preview/
sos build coffee_shops                     # 4096x2048 -> dist/coffee_shops/
sos still lunar_phases 180 --preview       # one PNG for a quick look
sos check where_the_world_plays            # fetch data + run checks, no rendering
sos build getting_richer --seconds 120     # another length -> dist/getting_richer_120s/
sos bundle where_the_world_plays           # zip every built folder, length and browser preview for a Release
sos site --dist dist --serve               # gallery + simulator + help at http://localhost:8040/
```

Deploy by copying `dist/<slug>/` to `/shared/sos/media/site-custom/<slug>/` on the SOS machine (the generated
README has the exact commands and checksums).

## The site and the simulator

`sos site` builds the site behind sos.stevenpenza.com:
- a gallery of the published datasets, each card showing its date range, and each movie with a play button for its
  flat preview. Movies with several lengths or text styles have **Length** and **Text** selects, which set what the
  preview plays, what the simulator opens and what Download fetches
- a **simulator** that plays a dataset on a virtual 6-foot sphere in the browser, following its `playlist.sos`
  (frame rate, holds, spin, tilt, and SOS labels drawn on the sphere where the sphere draws them). It has Orbit and
  Visitor views and the same Length and Text selects, and you can drop in a dataset folder of your own, or a 360
  video from your camera, to see it on the sphere (design/v0/v0.8.0.md); nothing is uploaded.
- the help, built by Ask & Answer, with its question box first, and an ask box on the home page that takes a
  question there. The release is pinned in `ask.lock.json` and fetched from the site's `ask_base`, each
  file checked against the lock. `sos site --no-ask` builds without it (a fork, say): the gallery and simulator stay,
  and Help links open the docs on GitHub

- **Suggest a dataset,** a form that sends a link, its licence and what it would show to the maintainer's mailbox
  through a small API on the site's own address, behind a honeypot and rate limits
  (design/v0/v0.6.0.md; `sos admin` switches whether they go there or to a
  test address, design/v0/v0.7.2.md)

- **Anonymous counts** of page views and Download clicks, with no cookie or identifier, when `[site] count` is on.
  Every five minutes a Lambda publishes them as `status/sos.json` for stevenpenza.com's dashboard (design/v0/v0.7.0.md)

The site's pages, their scripts and `sos site` live here. Its deployment doesn't: the CDK stack and its API, the live
`site.toml` and the release that deploys it are in a private repo, sos-site, which builds the site from a pinned
sos-forge tag (design/v0/v0.9.0.md). `sos site`, `sos upload-help` and `sos admin` read `./site.toml`, so run them
from there.

Every animation also carries `labels.txt`, one line of text per frame. SOS can draw it as fixed labels around the
sphere: set `[hud] mode = "labels"`, or switch them on from the iPad.

## Bundled recipes

| Recipe | Output | Data |
|---|---|---|
| `lunar_phases` | 24 s loop: the Sun circles the tidally locked Moon over 29.5 days, with relief shading and cast shadows | NASA SVS CGI Moon Kit (LRO LROC + LOLA) |
| `where_the_world_plays` | 36 s loop spotlighting where eight sports are played | OpenStreetMap via Overpass (ODbL) |
| `aurora_forecast` | Still image: NOAA's aurora forecast over the real day and night at build time, named with its date | NOAA SWPC OVATION (public domain) |
| `getting_richer` | 40 s, 1820 to 2022: average income per person in every country, on a log colour scale with a legend | Maddison Project Database 2023 via Our World in Data (CC BY 4.0); Natural Earth |
| `living_longer` | 30 s, 1950 to 2023: life expectancy at birth in every country, with a legend | Our World in Data (UN WPP and others, CC BY 4.0); Natural Earth |
| `cities_growing` | 22 s, 1975 to 2030: about 11,700 urban centres glowing by population, with projections marked | GHSL Urban Centre Database R2024A, European Commission JRC (CC BY 4.0) |
| `earthquakes_this_week` | Still image: every earthquake of M4.5 or more in the 7 days before the build, over the seafloor, named with its date | USGS earthquake feed (public domain) |
| `fires_burning_now` | Still image: every fire detected from orbit in the 24 hours before the build, named with its date | NASA FIRMS, VIIRS on NOAA-20 and NOAA-21 |
| `where_people_live` | Still image: how many people live where, 8.2 billion in 2025, on a log scale with a legend | GHS-POP R2023A, European Commission JRC (CC BY 4.0) |
| `my_activities` | Still image: your GPS tracks glowing on the globe, with a zoomed inset | Your own GPX/TCX/FIT files (e.g. a fitness app's bulk export) |
| `my_360_video` | Your 360 camera's video as a movie, as long as the clip, with its title as SOS labels | Your own stitched 360 video |

## A recipe in 20 lines

```toml
[dataset]
name = "Coffee Shops"
description = "Shown to presenters in the SOS playlist."
output = "texture"                 # or "movie" with an [animation] table
credits = ["My survey data"]

[[layers]]
type = "basemap"
name = "black_marble"              # NASA night lights via GIBS; or blue_marble, path = "x.jpg", color = "#000000"
brightness = 0.5

[[layers]]
type = "points"
source = { type = "csv", path = "data/places.csv" }
weight = "visitors"
color = "#ffb000"

[hud]
title = "Coffee Shops"
subtitle = "{count:,} places"
```

See [docs/recipe-reference.md](docs/recipe-reference.md) for every option, the layer and source types, HUD templates, and how to add
custom layers in a `recipe.py`.

## How it works

- **Projection.** Pixel centres map to lon = ((x+0.5)/W)·360−180 and lat = 90−((y+0.5)/H)·180. Lon 0 sits at X = W/2.
  Every blur and resample wraps across ±180° so there is no seam.
- **Glow on a sphere.** Point and track density uses a Gaussian with a fixed size on the ground. Its horizontal
  width grows as 1/cos(lat), so dots stay round on the globe at every latitude.
- **Linear light.** Layers composite in linear light and are encoded to sRGB once.
- **Presenter HUD.** Panels are drawn flat and warped onto the sphere through a gnomonic projection, so text isn't
  stretched by latitude. They repeat at configurable longitudes so both sides of the globe can read them.
- **Caching.** Each layer's heavy arrays are cached per resolution and input fingerprint in `build/`. Downloads are
  cached in `~/.cache/sosforge` (override with `$SOSFORGE_CACHE`).
- **Parallelism.** Spawned worker processes memory-map the caches, and a bounded queue feeds ffmpeg.
- **SOS encoding.** Each movie is rendered once and encoded twice:
  - **Primary, `dist/<slug>/`:** MPEG-4 Part 2 at 50 Mbps for 4K (never below 25), as NOAA's content guide
    recommends. The guide warns that H.264 can cause errors in some SOS versions.
  - **Variant, `dist/<slug>_h264/`:** H.264 Main, CRF 18, with BT.709 tags, so a site can compare both on its
    sphere.

  Both are yuv420p with no B-frames, a keyframe every second, and `+faststart`. Use `--codec mpeg4` to build only
  one, or set `[encode] codecs` in a recipe.

## Credits and licences

Every dataset carries the credits and licence notes its sources require, in its `playlist.sos` description and
its README. [docs/data-sources.md](docs/data-sources.md) lists the sources the bundled recipes use. Downloads are
cached in `~/.cache/sosforge`, and OpenStreetMap queries are cached for 30 days so a rebuild doesn't repeat them.

## Development and releases

```bash
git config core.hooksPath tools/hooks   # once per clone: blob-size and scope-doc pre-commit gate
cp .env.example .env.local              # tokens live here (gitignored); `sos` loads it
pytest                                  # 146 tests, 9 of them in headless Chrome when Playwright is installed
node --test site/js/*.test.mjs          # simulator logic
pip install -e ".[browser]"             # once: Playwright, which drives your installed Google Chrome
python -m pytest tests/test_browser.py  # the built site in headless Chrome, under the deployed CSP
python tools/smoke.py https://sos.stevenpenza.com   # the live site: clean pages, every download answers
```

- Commits go straight to `main` with the subject `vX.Y.Z: <one sentence>.`
- Every version has a scope doc at `design/v<major>/vX.Y.Z.md` saying what the version decides, what it doesn't,
  and how it was tested. The doc is committed with its code and bumped by hand in `pyproject.toml` and
  `sosforge/meta.py`. The pre-commit hook refuses code without a scope doc.
- **What's new** on the site lists each released version's `## For visitors` section: bullets of the form
  `- <Kind>: <what a visitor would notice>`, where Kind is New dataset, Updated dataset, Gallery, Simulator, Help or
  sos-forge. Leave the section out of a version visitors wouldn't notice, and leave formatting out: text sizes,
  spacing, colours and widths. A version shows once its tag exists, dated by the tag.
- Pushes to `main` only run tests (`.github/workflows/test.yml`).
- **Only a pushed tag releases**:
  ```bash
  git tag -a v0.1.0 -m "v0.1.0: <one sentence>"
  git push origin v0.1.0
  ```
  `release.yml` then runs `tools/release.sh` in the `production` environment, the same script you can run by
  hand. It refuses an unscoped or mismatched tag, then tests, builds and creates the GitHub Release.
- **Rendered datasets go on a data release first**, on this repo, so sos-site's deploy can download them:
  `sos bundle <slug>`, then `gh release create data-YYYY-MM <zips>`, then list them in sos-site's `site.toml`. A
  `data-*` tag deploys nothing. Extra lengths are built with `sos build <slug> --seconds N`, bundled by the same
  `sos bundle`, and listed as `lengths = [N, ...]`. For every length and text style other than the standard one,
  `sos bundle` also writes `<version>_web.zip`: a 2048-wide H.264 preview with its `labels.txt`, which the cards and
  the simulator play. Upload those too. `sos site` refuses to deploy a download, length or preview that its release
  doesn't hold.
- **Help's docs reach Ask & Answer only through sos-site's release.** After the site deploys, it runs
  `sos upload-help` from its checkout of the pinned tag, which uploads that tag's docs with the pinned `corpus.py`
  and sos-site's upload key. A docs change is live on Help once a version holding it is released here and sos-site
  moves its pin to it.
- **The architecture chart** for stevenpenza.com's owner page is drawn and uploaded by sos-site's release.
- **Moving to another Ask & Answer release** is deliberate, never part of a build: take `corpus.py` from the API's
  `artifacts.base`, check it against the integrity `GET /ask` lists, run
  `python3 corpus.py --project . --lock <ask_base>`, and commit the new `ask.lock.json` with the version that moves.
- **Submitting a dataset to NOAA's SOS Data Catalog** happens on `sos admin`, a page served only on 127.0.0.1:8041
  with your own AWS login, through the aws CLI.
  - **Drafts:** "Submit to NOAA" drafts the email NOAA's catalog page asks for, from the live `datasets.json`:
    description, notable features, credits and every zip's link. You fill in its `[[...]]` line, then send it to
    `sos.support@noaa.gov` through SES.
  - **The record:** each email goes with a copy to the sending address. The page files that copy and every reply
    under its dataset, from the S3 prefix where that address's receipt rule stores its mail.
  - **Safety:** sending stays off until such a rule exists, so a reply can't be lost. The page can only write to
    NOAA's address, or to your own test address.
  - **A test first:** "Send a test to …" sends the same email to the address in `SOSFORGE_TEST_TO` instead of NOAA,
    marked as a test and without the copy, so nothing is filed and the dataset stays unsent (design/v0/v0.7.3.md).
  - **Settings:** in `.env.local`, which git ignores: `SOSFORGE_ADMIN_FROM`, `SOSFORGE_ADMIN_NAME`,
    `SOSFORGE_MAIL_BUCKET` and `SOSFORGE_MAIL_PREFIX`, and optionally `SOSFORGE_TEST_TO`.
  - **Acceptance:** when NOAA accepts a dataset, "Record catalog link" writes `noaa_catalog = "<its catalog page>"`
    into sos-site's `site.toml`. Commit that there, and the card links it. The correspondence never enters a repo.

MIT licence. Built for NOAA SOS sites, museums and science centres.
