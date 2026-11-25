# Recipe reference: every setting

A recipe is a folder holding `recipe.toml` and, optionally, a `recipe.py` and a `data/` folder.

`sos build <name>` looks for the recipe in this order:
1. `./recipes/<name>/`
2. the bundled `recipes/` folder

You can also pass a folder or a TOML path. Relative paths inside a recipe resolve against the recipe folder.
Unknown keys are errors, and the error suggests the closest valid key.

## `[dataset]`

| Key | Default | Meaning |
|---|---|---|
| `name` | required | Playlist name shown to presenters. It can name build values as HUD templates do, such as `{forecast_time:%b %Y}`, filled from the first frame; it then needs a `slug` |
| `slug` | from name | Folder name in `dist/` and on the SOS machine |
| `stem` | slug | Data file stem; the size tag is appended (e.g. `moon_phases_4k.mp4`) |
| `description` | `""` | One or two sentences; credits are appended automatically |
| `output` | `"texture"` | `texture` (one image) or `movie` |
| `width` | `4096` | Output width; height is width/2; must be a multiple of 4. `--preview` forces 1024 |
| `image_format` | `"jpg"` | `jpg` (quality 95) or `png`, for textures |
| `credits` | `[]` | Extra credit lines; layers add their own |
| `license` | `""` | Licence statement for the dataset |

## `[animation]` (movies)

| Key | Default | Meaning |
|---|---|---|
| `frames` | `720` | Frame count. `0` takes the length from a `video` layer: as long as the clip, at its own speed |
| `fps` | `30` | Frame rate (also written to `playlist.sos`) |
| `loop` | `true` | `true`: frame i is at i/frames, never repeating frame 0. `false`: the last frame is the final state |
| `start`, `end` | none | Dates (`"YYYY-MM-DD"` or `"today"`) mapped across the frames; enables time-based layers |

## `[hud]` presenter panel

| Key | Default | Meaning |
|---|---|---|
| `enabled` | `true` | Draw the panel |
| `title`, `subtitle` | `"{name}"`, `""` | Python format templates (see values below) |
| `icon` | none | A registered icon name (e.g. `moon_phase` from the lunar recipe) |
| `progress`, `progress_ticks` | `false`, `[]` | Progress bar of the animation fraction, plus tick marks |
| `anchors` | `[0, 180]` | Longitudes where the panel is repeated |
| `lat`, `width_deg`, `height_deg` | `-30`, `40`, `10` | Placement and angular size on the sphere |
| `font` | system | Path to a TTF/OTF |
| `fit_samples` | `96` | Frames sampled to fit font sizes, so text never jumps between frames |

**Template values:**
- Every frame has `name`, `frame`, `frames`, `fraction` and `date`, plus `year` when `[animation]` has dates. Use for
  example `{date:%B %Y}` or `{fraction:.0%}`.
- Layers add their own values:
  - points: `count`, `weight_sum`, `category`, `categories`
  - tracks: `count`, `distance_km`, `span_km`
  - lit_globe: `sun_lon_deg`
  - daylight: `sun_time`, `sun_lat`, `sun_lon`
  - grid: the source's times (the aurora forecast has `forecast_time` and `observation_time`) and `grid_peak`
  - regions: `year`, `regions_with_data`
- A recipe hook can add anything else.

## `[encode]`

| Key | Default | Meaning |
|---|---|---|
| `codecs` | `["mpeg4", "h264"]` | Encodings to build from the same frames. The first is the primary and goes to `dist/<slug>/`; each extra one goes to `dist/<slug>_<codec>/` with its own playlist (name suffixed, e.g. "(H.264)") |
| `crf`, `preset` | `18`, `slow` | H.264 quality and speed |
| `bitrate` | 50M at 4K | MPEG-4 Part 2 target; at least 25M, NOAA's minimum |

MPEG-4 Part 2 comes first because NOAA's content guide recommends it and warns that H.264 "can cause errors in the
SOS software". H.264 is built alongside until a site confirms which codec its SOS plays. Every movie is yuv420p
with no B-frames, a keyframe every second and `+faststart`; H.264 also carries BT.709 tags. `--codec` on the
command line (repeatable) overrides the list.

## `[playlist]`

Extra SOS `playlist.sos` keys, written verbatim. Examples: `tiltx`, `animate`, `subcategory`, `lastdwell`,
`zrotationenable`. Unknown keys produce a warning. `name`, `description`, `data` (and `fps` for movies) are generated.

## `[assets.<name>]`

Remote files downloaded once to the shared cache and validated: `url`, plus optional `size`, `sha256` and
`filename`. Layers refer to them by `<name>`.

## Layers (`[[layers]]`, drawn in order)

### `basemap`

Set exactly one of:
- `name`: `black_marble`, `blue_marble` or `blue_marble_relief` (NASA GIBS, fetched at the output width)
- `path`: an image file or asset name
- `color`: `"#rrggbb"`

Optional: `brightness`, `saturation`, `opacity`, `credit`.

### `video`

A 360 video as the movie's picture: the stitched 2:1 video a 360 camera's app exports (MP4, MOV, M4V, WebM or MKV),
decoded at the output size.
- `path`: the video, or a pattern such as `"data/*"` that matches exactly one.
- Optional: `credit`, for the footage.

The whole clip spans the movie. With `[animation] frames = 0` the movie is as long as the clip, so it plays at its
recorded speed; `sos build --seconds N` fits the whole clip into N seconds. A video that isn't 2:1 is refused. The
`my_360_video` recipe is a ready-made one.

### `points`

| Key | Default | Meaning |
|---|---|---|
| `source` | required | A source table (below) |
| `weight`, `weight_scale` | none, `linear` | Numeric column for brightness; `linear`, `sqrt` or `log` |
| `weight_series` | none | A column template such as `"pop_{year}"`. Weights follow the frame's year, interpolated between columns, and points stay dark until they have weight. Replaces `weight` and `time` |
| `weight_ref` | none | The weight drawn at full strength: a number, or `"auto"` for the 99th percentile. Use it when weights are large numbers such as populations |
| `time` | none | Date column; with `[animation] start/end` points appear on their date |
| `flash_days`, `flash_color` | `0`, white | New points flash for this many days of story time |
| `category`, `categories`, `palette` | none | Category column, optional explicit order, and `{ Name = "#hex" }` colours |
| `color` | `#ffb000` | Colour when there is no category |
| `cycle`, `cycle_all_label`, `cycle_dim`, `cycle_fade` | `false` | Spotlight each category in turn, then show all |
| `radius_km`, `halo_km`, `core_gain`, `halo_gain`, `halo_strength`, `intensity` | 15, 80, 0.9, 0.08, 0.6, 1 | Glow shape |
| `body_radius_km` | Earth | Set to 1737.4 for Moon data, 3389.5 for Mars |

### `tracks`

| Key | Default | Meaning |
|---|---|---|
| `source` | required | Usually `{ type = "tracks", path = "data/**/*" }` |
| `privacy_trim_m` | `400` | Trims the start and end of each track (raw exports include your home) |
| `width_km`, `gain`, `colormap`, `intensity` | 6, 1.2, `fire`, 1 | `colormap` is `fire`, `ice` or `"#rrggbb"` |
| `inset`, `inset_lon`, `inset_lat`, `inset_size_deg`, `inset_label` | `true`, opposite side, 20, 36 | Zoomed local view |

### `lit_globe`

An albedo map lit by a Sun that circles the equator, with DEM relief normals and horizon-map cast shadows:
- `color` and `elevation` (asset names or paths), `radius_km`
- `elevation_units`: `km`, `m` or `svs_uint16`; or `elevation_scale` plus `elevation_offset_m`
- shading: `relief`, `shadows`, `shadow_max_km`, `ambient` (linear light), `brdf` (`lambert` or `lommel-seeliger`)
- Sun path: `sun_start_lon_deg`, `sun_direction` (`west` or `east`), `sun_cycles`, `sun_lat_deg`

### `daylight`

Day imagery where the Sun is up and night imagery where it is down, blended through twilight, for the Sun's real
position:
- `day`, `night`: basemap names or image paths (defaults `blue_marble` and `black_marble`)
- `day_brightness`, `night_brightness`, `twilight_deg` (default 12)
- `at`: `timeline` (each frame's date; needs `[animation] start` and `end`), `now` (the build time), or an ISO
  date-time such as `"2026-12-21T12:00:00Z"`

### `grid`

A gridded field, such as the aurora forecast or where people live, drawn as light or paint.

| Key | Default | Meaning |
|---|---|---|
| `source` | required | A gridded source (`swpc_aurora`, `ghsl_pop`) |
| `colormap` | `aurora` | `fire`, `ice`, `aurora`, `wealth`, `sea` or `"#rrggbb"` |
| `min`, `max`, `gamma` | data range, 1 | The values mapped onto the colour ramp |
| `scale` | `linear` | `log` gives each tenfold rise the same step up the ramp; `min` and `max` must then be above 0, and values under `min` stay dark |
| `blend`, `opacity` | `add`, 1 | `add` glows as light; `over` paints over the layers below, covering more where values are higher |
| `smooth_km` | 0 | Softens a coarse grid |
| `legend`, `legend_title`, `legend_format` | `false`, none, `"{:,.0f}"` | A colour bar with round ticks, drawn at the HUD's anchors |
| `legend_lat`, `legend_anchors` | -46, the HUD's | Where the legend sits |

The source's own numbers, such as `ghsl_pop`'s `people` and `people_billions`, are HUD values.

### `regions`

Countries, or any polygons, shaded by a value that changes year by year, with borders and a colour-bar legend.

| Key | Default | Meaning |
|---|---|---|
| `regions` | required | A region source (`natural_earth`, `geojson_regions`) |
| `data` | required | A table of code, year and value (`owid`, `table`) |
| `scale`, `min`, `max` | `linear`, 2nd and 98th percentile | `log` also interpolates between data years geometrically, so steady growth looks steady |
| `colormap`, `opacity` | `wealth`, 0.85 | |
| `no_data_color`, `no_data_opacity` | grey, 0.55 | Regions with no value that year |
| `border_color`, `border_opacity` | white, 0.22 | |
| `year` | the frame's date | Fixes one year instead |
| `legend`, `legend_title`, `legend_format`, `legend_lat`, `legend_anchors` | on, the data's unit, `{:,.0f}`, −46, the HUD anchors | The colour-bar panels |

Values are interpolated between data years and held at most one year past a region's first and last value. Regions
that share a code, such as a country and its territories, share its value.

## Sources (`source = { type = ... }`)

| Type | Keys | Notes |
|---|---|---|
| `csv` | `path`, `lat`, `lon`, `delimiter`, `attribution`, `license` | Every other column is kept for weight, time and category |
| `geojson` | `path`, `attribution`, `license` | Point and MultiPoint features; properties become columns |
| `tracks` | `path` (glob), `attribution` | GPX, TCX, FIT (needs `pip install 'sos-forge[fit]'`) and `.gz` versions; GeoJSON lines |
| `overpass` | `queries = [{ category, query }]`, `endpoint`, `max_age_days`, `timeout_s`, `pause_s` | Overpass QL statements; output is cached and paced |
| `ghsl_ucdb` | none | About 11,700 urban centres with population every five years from 1975 to 2030 (GHSL R2024A; 2030 is projected). Columns `name`, `country` and `MT_POP_TOT_<year>`. Downloads 37 MB once |
| `firms` | `satellites` (`noaa-20`, `noaa-21`), `period` (`24h`, `48h`, `7d`), `min_confidence` (`nominal`), `max_age_minutes` (60) | NASA FIRMS's VIIRS active fires from its open files, cut to the period before the latest detection; columns `frp` (fire radiative power, MW), `confidence`, `daynight`, `satellite`, `time`. Its times `fires_from` and `fires_to` are HUD values. For `points` layers |
| `usgs_quakes` | `feed` (`4.5_week`), `max_age_minutes` (10) | The USGS's earthquake summary feeds, named `<level>_<span>`: a level from `significant`, `4.5`, `2.5`, `1.0`, `all` and a span from `hour`, `day`, `week`, `month`. Columns `mag`, `depth_km`, `place`, `time` and `strength` (10^((mag − 4.5) / 2), about three times brighter per magnitude). Its times `quakes_from` and `quakes_to` and its number `strongest` are HUD values. For `points` layers |
| `ghsl_pop` | `cells_per_degree` (12) | People per km² in 2025 from GHSL's population grid (GHS-POP R2023A), summed from 30 arcseconds into cells of 1/12° (about 9 km), with the total as `people` and `people_billions`. Downloads 484 MB once. For `grid` layers |
| `swpc_aurora` | `max_age_minutes` | NOAA's OVATION aurora forecast: a 1° grid of the 0–100 % chance of aurora overhead. Cached 30 minutes. For `grid` layers |
| `natural_earth` | `scale` (`50m` or `110m`) | Country shapes keyed by ISO 3166 alpha-3 code. For `regions` layers |
| `geojson_regions` | `path`, `code`, `name`, `attribution`, `license` | Your own polygons. For `regions` layers |
| `owid` | `slug`, `column`, `max_age_days` | Any Our World in Data chart, named as in its URL (`ourworldindata.org/grapher/<slug>`), with its citation. For `regions` data |
| `table` | `path`, `code`, `year`, `value`, `attribution`, `license` | Your own CSV of codes, years and values. For `regions` data |

## Custom code: `recipe.py`

Anything a TOML layer can't do goes in `recipe.py`, which is imported before the layers are checked:

```python
from sosforge import Check, Layer, register_icon, register_layer, register_source

@register_layer("my_layer")
class MyLayer(Layer):
    """One line describing it. One line on how it is tested."""
    OPTIONS = {"level": 0.5}                      # TOML keys and defaults; unknown keys are rejected
    def prepare(self):  self.store.put("img", ...) # once, in the main process, cached per resolution
    def attach(self):   self.img = self.store.get("img")   # in every worker (memory-mapped)
    def render(self, canvas, t): canvas += self.img        # linear-light float32 (H, W, 3)
    def values(self, t): return {"thing": 1}               # for HUD templates

def values(t, ctx):          # extra HUD values for every frame (t.fraction, t.date, t.index)
    return {"day": 29.53 * t.fraction}
# or def values(t, ctx, vals): to also see the layer values, e.g. vals["weight_sum"]

def checks(ctx, layers):     # extra verification; failures stop strict builds
    return [Check("my check", True, "detail")]
```

The `lunar_phases` recipe is a complete example: it adds phase names, a phase icon and landmark checks.
`cities_growing` shows the three-parameter `values` hook turning a population total into words.
