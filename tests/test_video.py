"""Tests for the video layer: a 360 video made into a movie for the sphere (sosforge/layers/video.py, design/v0/v0.8.4.md)."""
from __future__ import annotations

import subprocess
from pathlib import Path

import cv2
import numpy as np
import pytest

from sosforge.build import BuildOptions, build
from sosforge.encode import find_ffmpeg
from sosforge.layers.video import VideoReader, find_video, probe
from sosforge.recipe import RecipeError, load_recipe


def clip(path: Path, size: str = "128x64") -> Path:
    """Write a 2-second H.264 video, red for its first second and green for its second, as a camera's app might export it. Helper for the tests below; not itself tested."""
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([find_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", f"color=c=red:s={size}:d=1:r=30", "-f", "lavfi", "-i", f"color=c=lime:s={size}:d=1:r=30",
                    "-filter_complex", "[0][1]concat=n=2:v=1", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)], check=True)
    return path


def recipe(folder: Path, layer: str, frames: int = 0, fps: int = 10) -> Path:
    """Write a recipe whose movie is the given layer, with its title as SOS labels. Helper for the tests below; not itself tested."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "recipe.toml").write_text(f"""[dataset]
name = "My Clip"
slug = "my_clip"
output = "movie"
width = 128
[animation]
frames = {frames}
fps = {fps}
loop = false
[[layers]]
{layer}
[hud]
mode = "labels"
title = "{{name}}"
""")
    return folder


def colour(rgb: np.ndarray) -> str:
    """Name a frame's middle as red, green or other. Helper for the tests below; not itself tested."""
    r, g, b = (int(v) for v in rgb[rgb.shape[0] // 2, rgb.shape[1] // 2])
    return "red" if r > 180 and g < 80 else "green" if g > 180 and r < 80 else f"other {r},{g},{b}"


def test_probe_reads_size_and_duration(tmp_path):
    """Checks probe reads a video's frame size, codec and duration, and refuses a file that isn't a video. Covers probe against real ffmpeg output; other containers and codecs are not tested."""
    info = probe(clip(tmp_path / "a.mp4"))
    assert (info["width"], info["height"], info["codec"]) == (128, 64, "h264") and abs(info["duration"] - 2.0) < 0.05
    (tmp_path / "notes.mp4").write_text("not a video")
    with pytest.raises(RecipeError, match="no video stream"):
        probe(tmp_path / "notes.mp4")


def test_find_video_needs_exactly_one(tmp_path):
    """Checks a video layer's path finds one video by glob or by name, ignores files that aren't videos, and refuses none or several, naming them. Covers find_video; the layer's own refusal without a path is tested by test_frames_zero_needs_a_video."""
    r = load_recipe(recipe(tmp_path / "r", 'type = "video"\npath = "data/*"'))
    with pytest.raises(RecipeError, match="no video found at data/"):
        find_video(r, "data/*")
    clip(tmp_path / "r" / "data" / "harbour.mp4")
    (tmp_path / "r" / "data" / "README.md").write_text("notes")
    assert find_video(r, "data/*").name == "harbour.mp4" and find_video(r, "data/harbour.mp4").name == "harbour.mp4"
    clip(tmp_path / "r" / "data" / "second.MOV")
    with pytest.raises(RecipeError, match=r"matches 2 videos \(harbour.mp4, second.MOV\)"):
        find_video(r, "data/*")


def test_reader_follows_the_clip(tmp_path):
    """Checks a reader returns the clip's frame for each movie frame, spread over the whole clip: forward, back again (a restart), far ahead, and past the end, where it holds the last frame. Covers VideoReader and reader_command against real decoding; the seek's exact accuracy is not tested beyond a second's colour."""
    reader = VideoReader(clip(tmp_path / "a.mp4"), 2.0, 20, 32, 16)
    try:
        got = [colour(reader.frame(i)) for i in (0, 3, 15, 5, 19, 25)]
        assert got == ["red", "red", "green", "red", "green", "green"]
        assert reader.frame(7).shape == (16, 32, 3)
    finally:
        reader.close()


def test_build_a_360_video(tmp_path):
    """Checks a recipe with a video layer and frames = 0 builds a movie as long as the clip at its recorded speed, in worker processes, with the clip's colours at the right times, its title as labels for every frame, the footage's credit, and a passing 2:1 check; and that --seconds fits the whole clip into that length. Covers VideoLayer, layer_frames and make_context's frames = 0 end to end; MPEG-4 and 4096-wide output are not tested here."""
    d = recipe(tmp_path / "r", 'type = "video"\npath = "data/*"\ncredit = "Footage: Pat Maker"')
    clip(d / "data" / "harbour.mp4")
    s = build(d, BuildOptions(out_dir=tmp_path / "dist", build_dir=tmp_path / "b", workers=2, codecs=["h264"]))
    assert all(c["passed"] for c in s["checks"]), [c for c in s["checks"] if not c["passed"]]
    assert any("2:1 360 frame" in c["name"] and "128x64 h264, 2.0s" in c["detail"] for c in s["checks"])
    assert (s["video"]["frames"], s["video"]["width"], s["video"]["height"]) == (20, 128, 64)
    out = tmp_path / "dist" / "my_clip"
    cap = cv2.VideoCapture(str(next(out.glob("*.mp4"))))
    frames = [cap.read()[1][..., ::-1] for _ in range(20)]
    assert [colour(frames[i]) for i in (1, 8, 12, 19)] == ["red", "red", "green", "green"]
    assert (out / "labels.txt").read_text().splitlines() == ["My Clip"] * 20
    assert "Footage: Pat Maker" in (out / "README.md").read_text()
    short = build(d, BuildOptions(out_dir=tmp_path / "dist", build_dir=tmp_path / "b", workers=1, codecs=["h264"], seconds=1))
    assert short["video"]["frames"] == 10
    cap = cv2.VideoCapture(str(next((tmp_path / "dist" / "my_clip_1s").glob("*.mp4"))))
    frames = [cap.read()[1][..., ::-1] for _ in range(10)]
    assert [colour(frames[i]) for i in (1, 8)] == ["red", "green"]


def test_refuses_a_flat_video(tmp_path):
    """Checks a video that isn't 2:1 is refused with what to export instead, before anything is encoded. Covers VideoLayer.prepare's check; this test is not itself tested."""
    d = recipe(tmp_path / "r", 'type = "video"\npath = "data/*"')
    clip(d / "data" / "flat.mp4", "160x90")
    with pytest.raises(RecipeError, match=r"160x90, not the 2:1 frame of a stitched 360 video"):
        build(d, BuildOptions(out_dir=tmp_path / "dist", build_dir=tmp_path / "b", workers=1, codecs=["h264"]))
    assert not (tmp_path / "dist" / "my_clip").exists()


def test_frames_zero_needs_a_video(tmp_path):
    """Checks frames = 0 is refused in a recipe with no layer that sets a length, and a video layer is refused without a path. Covers layer_frames and VideoLayer's option check; this test is not itself tested."""
    d = recipe(tmp_path / "a", 'type = "basemap"\ncolor = "#336699"')
    with pytest.raises(RecipeError, match="frames = 0 takes the length from a layer"):
        build(d, BuildOptions(out_dir=tmp_path / "dist", build_dir=tmp_path / "b", workers=1))
    d = recipe(tmp_path / "b", 'type = "video"', frames=10)
    with pytest.raises(RecipeError, match="needs a path"):
        build(d, BuildOptions(out_dir=tmp_path / "dist", build_dir=tmp_path / "b2", workers=1))
