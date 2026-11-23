# Make your first dataset

sos-forge turns a short recipe into a dataset you can copy straight onto a Science On a Sphere. This page goes
from nothing to a dataset on your screen in a few minutes.

## Install

You need Python 3.11 or newer.

```bash
git clone https://github.com/slpenza/sos-forge.git
cd sos-forge
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
```

You don't need to install ffmpeg separately; one comes with sos-forge. If you already have ffmpeg on your path,
sos-forge uses yours.

## Try a bundled dataset

```bash
sos list                                  # what's available
sos build lunar_phases --preview          # a quick 1024 × 512 version, about 15 seconds
```

`--preview` builds small and fast, so you can check a dataset before spending minutes on the full 4096 × 2048
version. The result lands in `dist/lunar_phases_preview/`.

To look at a single moment without building a video, render one frame as a picture:

```bash
sos still lunar_phases 180 --preview      # frame 180 of 720: First Quarter
```

## Make your own

```bash
sos new "Coffee Shops"
sos build coffee_shops --preview
```

`sos new` creates `recipes/coffee_shops/` with a recipe and a small sample spreadsheet of world cities, so the
first build works straight away. Replace `data/places.csv` with your own places. It needs `lat` and `lon`
columns, and anything else can come along, such as a number to make some places brighter.

Open `recipe.toml` to change what it shows. The comments explain each line, and
[the recipe reference](recipe-reference.md) lists every option. A misspelled setting stops the build and
suggests the right one, so you can experiment freely.

## Build it full size

```bash
sos build coffee_shops
```

This writes `dist/coffee_shops/`, the folder you copy to the sphere. See
[Put a dataset on your sphere](deploying.md).

## What a build checks

Every build checks its own work before it writes anything:
- the picture is exactly twice as wide as it is tall
- nothing breaks at the date line, where the left and right edges meet on the sphere
- the data actually loaded
- for videos, that every frame decodes

If a check fails, the build stops and says which one. [When a build stops](troubleshooting.md) explains each
check.
