# When a build stops

sos-forge stops rather than hand you a dataset that will look wrong on the sphere. The message says what went
wrong. Find it below.

## A setting is misspelled

`[dataset] unknown key 'widht' (did you mean 'width'?)`

Fix the spelling in `recipe.toml`. Every section and layer lists its valid settings in
[the recipe reference](recipe-reference.md).

## A check failed

The build prints every check as PASS or FAIL before stopping.

| Check | What it means | What to do |
|---|---|---|
| frame 0 seamless at +/-180 | The left and right edges don't match, so a line would show where they meet on the sphere | Your background image may not be a whole-world equirectangular image (2:1, −180° to 180°). Use one that is |
| points has data / tracks has activities | Nothing usable came from the source | Check the file path and its lat/lon columns. For tracks, check the files aren't all shorter than the privacy trim |
| Moon: … | The Moon maps aren't the expected NASA maps or orientation | Don't swap the Moon files unless you mean to |
| video … | The finished video isn't what SOS needs (wrong size, missing frames) | Usually a disk or encoder problem. Rebuild, and keep the `.ffmpeg.log` next to the video if it repeats |

To build anyway and look at the result, add `--no-strict`. The README still lists the failed checks.

## OpenStreetMap is slow or says it's busy

`HTTP 502 … retry 1/6`

The public Overpass server is shared by everyone and is often busy. sos-forge waits and retries on its own,
doubling the wait each time. A big recipe like Where the World Plays can take 20 minutes the first time. After
that the results are kept for 30 days, so rebuilding is instant. If you build often, run your own Overpass
server and point `SOSFORGE_OVERPASS_URL` at it.

## Pinball Map needs a token

`Pinball Map needs an API token`

Request one at pinballmap.com/api_token. Approval is by hand, so it can take a while. When it arrives, put it in
a file called `.env.local` next to your recipes:

```
PINBALLMAP_API_TOKEN=your-token-here
```

`.env.example` lists every setting the file can hold. `.env.local` never goes into the repository.

## No activity files found

`no activity files match …`

My Activities reads GPS files you put in `recipes/my_activities/data/`. Most fitness apps and GPS watches can
export your activities as files:
1. Look in your app's account or privacy settings for an option to download or export your data.
2. When the export is ready, download and unzip it.
3. Copy the activity files, or the folder that holds them, into `data/`.

GPX, TCX and FIT files all work, zipped (`.gz`) or not.

## A panel shows the wrong text, or none

`HUD template '{count:,} places' uses {count} but available values are: …`

The panel's text can only use values the dataset provides. The message lists them.

## It's slow

- Use `--preview` while you're adjusting a recipe.
- A full 4096 × 2048 animation takes about 5 minutes on an 8-core computer. Most of that is video encoding.
- `--workers` sets how many frames render at once. The default leaves half your computer free for the encoder.
