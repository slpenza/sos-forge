# Make your first dataset

sos-forge turns a short recipe into a dataset you can copy straight onto a Science On a Sphere. This page goes
from nothing to a dataset on your screen in a few minutes, then to one made from your own data.

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

Other bundled datasets show the other kinds of data sos-forge handles: `aurora_forecast` (a live forecast grid),
`getting_richer` (countries shaded year by year) and `cities_growing` (points that grow).

To look at a single moment without building a video, render one frame as a picture:

```bash
sos still lunar_phases 180 --preview      # frame 180 of 720: First Quarter
```

## Make a dataset from your own data

You can build a dataset from your own files. Three kinds work without writing any code:

| Your data | What it becomes | Start from |
|---|---|---|
| Places: a CSV with `lat` and `lon` columns | Glowing points, brighter where a number is bigger | `sos new`, below |
| A value for each country, or for regions of your own: a CSV of code, year and value | Regions shaded by the value, changing year by year in a movie | The `regions` layer in step 2 |
| GPS tracks: GPX, TCX or FIT files from a watch or fitness app | Your routes glowing on the globe, with a zoomed inset | The bundled `my_activities` |

1. **Start a recipe.**

   ```bash
   sos new "Coffee Shops"
   sos build coffee_shops --preview
   ```

   `sos new` creates `recipes/coffee_shops/` with a recipe and a small sample spreadsheet of world cities, so the
   first build works straight away.
2. **Put your data in.** Replace `data/places.csv` with your own places. It needs `lat` and `lon` columns, and
   anything else can come along, such as a number to make some places brighter. For regions, replace the
   `points` layer in `recipe.toml` with this one:

   ```toml
   [[layers]]
   type = "regions"
   # countries, by ISO 3166 alpha-3 code such as BRA
   regions = { type = "natural_earth" }
   # a CSV with the columns code, year and value
   data = { type = "table", path = "data/values.csv" }
   ```

   There are no places to count, so also change `subtitle` under `[hud]` to
   `"{regions_with_data} countries with data"`. For regions of your own, use
   `regions = { type = "geojson_regions", path = "data/areas.geojson" }`, with a `code` and a `name` property on
   each polygon. For GPS tracks, copy your files into `recipes/my_activities/data/` and build `my_activities`
   instead.
3. **Choose what it shows.** Open `recipe.toml`. The comments explain each line, and
   [the recipe reference](recipe-reference.md) lists every layer, source and option. A misspelled setting stops
   the build and suggests the right one, so you can experiment freely.
4. **Credit your source.** Replace the `credits` line with where your data came from. Credits travel with the
   dataset, in its playlist description and README. See
   [Where the data comes from, and what you may do with it](data-sources.md).
5. **Check it on a sphere.** Open the folder `sos build` made in [the simulator](simulator.md), and look at it
   from where visitors stand before you copy it anywhere.
6. **Build it full size and copy it to your sphere.**

   ```bash
   sos build coffee_shops
   ```

   This writes `dist/coffee_shops/`, the folder you copy to the sphere's site-custom area. See
   [Put a dataset on your sphere](deploying.md).

Your dataset stays on your computer and your sphere; it isn't added to this site's gallery. sos-forge can also
fetch OpenStreetMap features or any Our World in Data chart for you, and anything the layers can't draw goes in a
`recipe.py`. [The recipe reference](recipe-reference.md) covers both.

## Make a movie longer or shorter

A movie's length is set in its recipe. To build another length without editing the recipe, give it in whole
seconds:

```bash
sos build getting_richer --seconds 120
```

This renders a fresh frame for every moment, so a slower version is just as smooth, not stretched. It is written
to `dist/getting_richer_120s/` and named "Getting Richer (2 min)" in the playlist. That way it can sit on the
sphere next to the standard length, and presenters can pick whichever fits their show.

To leave the text out of the picture and let SOS draw it from `labels.txt` instead, so it can stay put while the
globe turns, add `--hud labels`. That version is written to `dist/getting_richer_labels/` (or
`dist/getting_richer_120s_labels/` with `--seconds 120`).

## What a build checks

Every build checks its own work before it writes anything:
- the picture is exactly twice as wide as it is tall
- nothing breaks at the date line, where the left and right edges meet on the sphere
- the data actually loaded
- for videos, that every frame decodes

If a check fails, the build stops and says which one. [When a build stops](troubleshooting.md) explains each
check.
