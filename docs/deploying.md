# Put a dataset on your sphere

A finished dataset is one folder in `dist/`. Copy it to the SOS computer, refresh the library, and it appears in
the playlist editor.

## What's in the folder

| File | What it is |
|---|---|
| The video or image | The dataset itself, for example `where_the_world_plays_4k.mp4` |
| `playlist.sos` | Tells SOS the name, description, credits, frame rate and tilt |
| `labels.txt` | Animations only: one line of text per frame, which SOS can show as fixed labels. See [Preview a dataset in the simulator](simulator.md) |
| `README.md` | Credits, licences, a checksum to confirm the copy, and the results of the build's checks |

Animated datasets come as two folders, one per video format. See [Which video file to use](video-formats.md).

## Copy it

Your own datasets go in the site-custom area of the SOS computer, one folder each:

```bash
rsync -av --chmod=Da+rx,Fa+r dist/where_the_world_plays/ sos@<sos-computer>:/shared/sos/media/site-custom/where_the_world_plays/
```

A USB drive works too. What matters is that the folder ends up under `/shared/sos/media/site-custom/` with its
files inside. Keep the folder's name. The playlist finds the video by name, in the same folder.

To make sure the copy is intact, compare its checksum with the one in the dataset's README:

```bash
sha256sum /shared/sos/media/site-custom/where_the_world_plays/where_the_world_plays_4k.mp4
```

## Make SOS see it

1. In the SOS Stream GUI, choose **Library → Update Library**.
2. On the iPad remote, open Settings and tap **Update Now**.
3. Find the dataset under **Site-Custom**, in its subcategory (for example Sports or Astronomy), and add it to a
   presentation.

## Changing how it plays

`playlist.sos` is plain text, and you can edit it on the SOS computer. Common changes:

| Line | Effect |
|---|---|
| `fps = 30` | Playback speed in frames per second |
| `tiltx = 6.68` | Tilts the globe, in degrees (Lunar Phases uses the Moon's tilt) |
| `lastdwell = 6000` | Holds the last frame for 6 seconds before looping |
| `animate = 1` | Starts playing as soon as the dataset loads |
| `labelvisible = true` | Shows the SOS labels (fixed text, four times around the sphere) |

To make a change permanent, put it in the recipe's `[playlist]` section and rebuild, so the next build doesn't
undo it.

## The presenter panels

Datasets carry small panels at 30° south, at about eye level for most people around a 6-foot sphere. They're
printed at several longitudes so people on every side can read one:
- **Earth datasets:** over open ocean (South Atlantic, Indian, South Pacific), so they never cover land.
- **Lunar Phases:** over the near side and the far side.
