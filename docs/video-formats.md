# Which video file to use

Every animated dataset is built twice from the same frames, as two folders:

| Folder | Video | Why it's there |
|---|---|---|
| `dist/<name>/` | MPEG-4 Part 2, about 50 Mbps at 4096 × 2048 | NOAA's content guide asks for it, and warns that H.264 "can cause errors in the SOS software" |
| `dist/<name>_h264/` | H.264, quality-based, usually much smaller | Most modern players expect it, and it's an easy comparison on your own sphere |

Both play at 30 frames a second, have a keyframe every second so presenters can scrub, and have no B-frames,
which some SOS decoders dislike. Both folders are complete datasets with their own playlist. The H.264 one is
named "<name> (H.264)" in the SOS playlist, so you can load them one after the other and compare.

## Which one to try first

Try the MPEG-4 one first; it's what NOAA documents. If it plays smoothly at full size, use it and ignore the
other folder.

Try the H.264 one if the MPEG-4 file stutters, looks blocky in dark gradients, or takes too long to copy.
Your SOS installation may play it perfectly well. NOAA's warning may date from older software. Nobody has
confirmed either way yet, which is why both are built.

## Watching it on a laptop

Web browsers don't play MPEG-4 Part 2. To check a dataset before taking it to the sphere, open the H.264 file
in a browser or any media player, or open either file in VLC.

## Why the MPEG-4 file is often smaller than 50 Mbps

50 Mbps is the encoder's target, not a minimum. Mostly-dark datasets, like glowing points on the night-side
Earth, reach the encoder's best quality at a fraction of that. The Where the World Plays video averages about
7 Mbps. NOAA's guide asks for at least 25 Mbps, so sos-forge never targets less. Whether NOAA means 25 Mbps as
a floor the file must actually reach is one of the questions we're asking.

## Building only one

To build just one format, list it in the recipe:

```toml
[encode]
codecs = ["mpeg4"]            # or ["h264"], or both; the first one goes in dist/<name>/
```

or pass `--codec mpeg4` (repeatable) to `sos build`.

## Still images

Still datasets (textures) are a single JPEG or PNG at 4096 × 2048 and have no video format to choose. NOAA
recommends 4096 × 2048 for textures, and its guide says images up to 16384 × 8192 still work.
