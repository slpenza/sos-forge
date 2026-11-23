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
| `name` | required | Playlist name shown to presenters |
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
| `frames` | `720` | Frame count |
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
- Every frame has `name`, `frame`, `frames`, `fraction` and `date`. Use for example `{date:%B %Y}` or `{fraction:.0%}`.
- Layers add their own values:
  - points: `count`, `weight_sum`, `category`, `categories`
  - tracks: `count`, `distance_km`, `span_km`
  - lit_globe: `sun_lon_deg`
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

### `points`

| Key | Default | Meaning |
|---|---|---|
| `source` | required | A source table (below) |
| `weight`, `weight_scale` | none, `linear` | Numeric column for brightness; `linear`, `sqrt` or `log` |
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

## Sources (`source = { type = ... }`)

| Type | Keys | Notes |
|---|---|---|
| `csv` | `path`, `lat`, `lon`, `delimiter`, `attribution`, `license` | Every other column is kept for weight, time and category |
| `geojson` | `path`, `attribution`, `license` | Point and MultiPoint features; properties become columns |
| `tracks` | `path` (glob), `attribution` | GPX, TCX, FIT (needs `pip install 'sos-forge[fit]'`) and `.gz` versions; GeoJSON lines |
| `overpass` | `queries = [{ category, query }]`, `endpoint`, `max_age_days`, `timeout_s`, `pause_s` | Overpass QL statements; output is cached and paced |
| `pinballmap` | `url`, `params`, `token_env`, `max_age_days` | Needs `$PINBALLMAP_API_TOKEN`; CC BY-SA 4.0 |

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

def checks(ctx, layers):     # extra verification; failures stop strict builds
    return [Check("my check", True, "detail")]
```

The `lunar_phases` recipe is a complete example: it adds phase names, a phase icon and landmark checks.
