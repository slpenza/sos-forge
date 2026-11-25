# Preview a dataset in the simulator

The simulator at sos.stevenpenza.com/simulator.html plays a dataset on a virtual 6-foot sphere in your browser, so
you can check it before it goes on a real one. It follows the dataset's `playlist.sos` the way the sphere does:
frame rate, the hold on the first and last frames, auto-play, spin, tilt and SOS labels.

## Look at a published dataset

Pick one from **Dataset**, or open it from a card on the front page.

A movie published at several lengths, or with SOS labels as well as painted text, shows **Length** and **Text**
under **Dataset**. A card's own **Length** and **Text** choices open that version. Switching keeps your place in the
movie, so you can rehearse a presentation at the length you'll show, and the address names the version, so a link
opens it again.

| Control | What it does |
|---|---|
| Drag sideways, or ← and → | Turns the picture about its poles, as a presenter does from the iPad. SOS labels stay put |
| Drag up or down | Looks from higher or lower |
| Scroll or pinch | Step closer or further away |
| **Orbit** | A view from a few metres away, showing the whole sphere |
| **Visitor** | Eye level for an adult standing 1.2 m from a 6-foot sphere. The text panels at 30° south sit at that height |
| **Spin** | Turns the dataset about its poles, as a sphere set to rotate would |
| **Labels** | Shows or hides the dataset's SOS labels on the sphere, as the iPad's Display Elements list does |
| Space | Play or pause |

## Check your own dataset

Choose **Open a dataset folder**, or drag the folder onto the sphere. Use a folder that `sos build` made. The
folder stays on your computer; nothing is uploaded.

Web browsers can't play MPEG-4 Part 2, the format NOAA recommends for the sphere. Open the folder ending in
`_h264` instead; it holds the same frames. See [Which video file to use](video-formats.md).

## Preview your own 360 video

Choose **Open a 360 video**, or drag the video onto the sphere. Use the stitched 360 video your camera's app exports,
as an MP4: a frame twice as wide as it is tall. It plays as it was recorded and stays on your computer; nothing is
uploaded.

- **Seen from outside.** A sphere shows the scene from outside, as a globe shows the Earth. The sky fills the top,
  the ground the bottom, and the camera's mount sits at the bottom pole. Footage shot outdoors from a high point
  works best.
- **Turning it.** Drag sideways, or use ← and →, to choose what faces the room, as with any dataset.
- **When it won't play.** A raw file straight from the camera holds two fisheye pictures, and needs the camera's app
  to stitch it first. Many 360 cameras record in H.265 (HEVC), which some browsers can't play: export H.264.
- **A warning** shows when the frame isn't 2:1, or is wider than your device's graphics can show.

The simulator only previews the video. To make a dataset from it for a real sphere, put it in
`recipes/my_360_video/data/` and run `sos build my_360_video`: see the recipe's own notes.

## Text that stays still

Datasets carry their text in two forms:

- **Panels printed into the picture**, at 30° south, several times around the globe. They look the same on every
  sphere, but they move when the picture moves: when a presenter rotates the globe, or when you drag in the
  simulator.
- **SOS labels**, a `labels.txt` file with one line per frame. The sphere draws these itself, once for each of its
  four projectors, so four times around. They stay put while the picture spins, tilts or is turned (in the
  simulator, by a sideways drag). The painted versions ship
  them hidden, and a presenter can switch them on from the iPad's Display Elements list. The SOS-labels versions
  show them.

The simulator draws the labels where the sphere does, and shows or hides them as the version's playlist says. Its
**Labels** button switches them, like the iPad. The playlist can place them:

| Playlist line | What it sets | Default |
|---|---|---|
| `labelvertical` | Degrees north of each projector's centre (negative is south) | −25 |
| `labelhorizontal` | Degrees east of each projector's centre | 0 |
| `labelheight` | Height in degrees of latitude; the width follows the text | not stated by NOAA; the simulator uses 4 |

Set them in the recipe's `[playlist]` table. A label wider than a quarter of the way round is cut off in the
simulator where it would meet the next copy.

To make labels the dataset's only text, with no panels printed into the picture, set this in the recipe:

```toml
[hud]
mode = "labels"
```

## What it can't show you

A real sphere is lit by four projectors whose edges blend, so brightness, colour and the seams between them differ
from your screen. Use the simulator to check content, timing and text, then confirm on the sphere itself.

The labels use SOS's default format, white Free Sans on a clear background. A site that changed it in NOAA's Label
Editor draws them differently, and NOAA gives no default height, so their size on a real sphere may differ.
