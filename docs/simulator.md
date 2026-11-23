# Preview a dataset in the simulator

The simulator at sos.stevenpenza.com/simulator.html plays a dataset on a virtual 6-foot sphere in your browser, so
you can check it before it goes on a real one. It follows the dataset's `playlist.sos` the way the sphere does:
frame rate, the hold on the first and last frames, auto-play, spin and tilt.

## Look at a published dataset

Pick one from **Dataset**, or open it from a card on the front page.

| Control | What it does |
|---|---|
| Drag | Walk around the sphere |
| Scroll or pinch | Step closer or further away |
| **Orbit** | A view from a few metres away, showing the whole sphere |
| **Visitor** | Eye level for an adult standing 1.2 m from a 6-foot sphere. The text panels at 30° south sit at that height |
| **Spin** | Turns the dataset about its poles, as a sphere set to rotate would |
| **Caption** | Shows the dataset's labels as a caption that stays put while you move |
| Space | Play or pause |

## Check your own dataset

Choose **Open a dataset folder**, or drag the folder onto the sphere. Use a folder that `sos build` made. The
folder stays on your computer; nothing is uploaded.

Web browsers can't play MPEG-4 Part 2, the format NOAA recommends for the sphere. Open the folder ending in
`_h264` instead; it holds the same frames. See [Which video file to use](video-formats.md).

## Text that stays still

Datasets carry their text in two forms:

- **Panels printed into the picture**, at 30° south, several times around the globe. They look the same on every
  sphere, but they move when the picture moves: when a presenter rotates the globe, or when you drag in the
  simulator.
- **SOS labels**, a `labels.txt` file with one line per frame. The sphere draws these itself, four times around,
  in a fixed position. They ship hidden; a presenter can switch them on from the iPad's Display Elements list.

The simulator's **Caption** button shows the same labels, fixed at the top of the view.

To make labels the dataset's only text, with no panels printed into the picture, set this in the recipe:

```toml
[hud]
mode = "labels"
```

## What it can't show you

A real sphere is lit by four projectors whose edges blend, so brightness, colour and the seams between them differ
from your screen. Use the simulator to check content, timing and text, then confirm on the sphere itself.
