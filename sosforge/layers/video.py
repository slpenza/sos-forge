"""A 360 video as a movie's picture: a camera's stitched equirectangular video, decoded by ffmpeg at the movie's size
and frame times (design/v0/v0.8.4.md). The whole clip spans the movie, so a movie as long as the clip plays it at its
recorded speed, and a --seconds build speeds it up or slows it down to fit."""
from __future__ import annotations

import atexit
import re
import subprocess
from pathlib import Path

import numpy as np

from ..checks import Check
from ..encode import find_ffmpeg
from ..grid import srgb_to_linear
from ..recipe import RecipeError
from ..registry import register_layer
from .base import Layer

VIDEO_SUFFIXES = (".mp4", ".mov", ".m4v", ".webm", ".mkv")
RATIO_TOLERANCE = 0.02   # a stitched 360 frame is 2:1; anything further off isn't one
JUMP_FRAMES = 90         # a reader this far behind seeks again instead of decoding its way forward
_LINEAR = srgb_to_linear(np.arange(256, dtype=np.float32) / 255.0)   # 8-bit sRGB to linear light, by table


def find_video(recipe, ref: str) -> Path:
    """Resolve a video layer's path, a file or a glob relative to the recipe such as data/*, to exactly one video file, refusing none or several by name. Tested by test_video.py::test_find_video_needs_exactly_one."""
    pattern = str(ref)
    if any(c in pattern for c in "*?["):
        found = sorted(p for p in recipe.dir.glob(pattern) if p.is_file() and p.suffix.lower() in VIDEO_SUFFIXES)
    else:
        p = recipe.resolve(pattern)
        found = [p] if p.is_file() else []
    if not found:
        raise RecipeError(f"no video found at {ref} (in {recipe.dir}): copy your 360 video there, as "
                          f"{', '.join(VIDEO_SUFFIXES)}")
    if len(found) > 1:
        raise RecipeError(f"{ref} matches {len(found)} videos ({', '.join(p.name for p in found)}): keep one, or name it")
    return found[0]


def probe(path: Path, ffmpeg: str | None = None) -> dict:
    """Return a video's duration in seconds, frame size and codec from ffmpeg's description of it, refusing a file with no video stream or no duration. Tested by test_video.py::test_probe_reads_size_and_duration."""
    res = subprocess.run([ffmpeg or find_ffmpeg(), "-hide_banner", "-i", str(path)], capture_output=True, text=True)
    text = res.stderr
    dur = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", text)
    stream = re.search(r"Stream #\S+.*?: Video: (\w+)[^\n]*?, (\d{2,5})x(\d{2,5})", text)
    if not stream:
        raise RecipeError(f"{path.name} has no video stream ffmpeg can read")
    if not dur:
        raise RecipeError(f"{path.name} has no duration; export it again from your camera's app")
    seconds = int(dur[1]) * 3600 + int(dur[2]) * 60 + float(dur[3])
    if seconds <= 0:
        raise RecipeError(f"{path.name} is empty")
    return {"duration": seconds, "width": int(stream[2]), "height": int(stream[3]), "codec": stream[1]}


def reader_command(ffmpeg: str, path: Path, start: float, rate: str, width: int, height: int) -> list[str]:
    """Return the argv that decodes a video from start seconds at rate frames a second, scaled to width x height, as raw RGB24 on stdout, with no sound. Tested by test_video.py::test_reader_follows_the_clip."""
    return [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-ss", f"{start:.6f}", "-i", str(path), "-an",
            "-vf", f"fps={rate},scale={width}:{height}:flags=area", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"]


class VideoReader:
    """Frames of one video at a movie's frame times, decoded forward by one ffmpeg and restarted with a seek when asked to go back or far ahead. Tested by test_video.py::test_reader_follows_the_clip."""

    def __init__(self, path: Path, duration: float, frames: int, width: int, height: int, ffmpeg: str | None = None):
        """Keep what the decoder needs; movie frame i shows the clip at i * duration / frames seconds. Tested by test_video.py::test_reader_follows_the_clip."""
        self.path, self.duration, self.frames = Path(path), float(duration), int(frames)
        self.width, self.height, self.ffmpeg = int(width), int(height), ffmpeg or find_ffmpeg()
        self.size = self.width * self.height * 3
        self.proc: subprocess.Popen | None = None
        self.next = 0
        self.last: np.ndarray | None = None
        atexit.register(self.close)

    def _start(self, index: int) -> None:
        """Start decoding at movie frame index, stopping any decoder already running. Tested through frame()."""
        self.close()
        rate = f"{self.frames}/{self.duration:.6f}"
        cmd = reader_command(self.ffmpeg, self.path, index * self.duration / self.frames, rate, self.width, self.height)
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.next = index

    def _read(self) -> np.ndarray | None:
        """Read the decoder's next frame, or None at the end of the clip. Tested through frame()."""
        buf = self.proc.stdout.read(self.size) if self.proc else b""
        if len(buf) < self.size:
            return None
        self.next += 1
        return np.frombuffer(buf, np.uint8).reshape(self.height, self.width, 3)

    def frame(self, index: int) -> np.ndarray:
        """Return movie frame index as uint8 sRGB, holding the clip's last frame past its end (the fps filter can round one short). Tested by test_video.py::test_reader_follows_the_clip."""
        if self.proc is None or index < self.next or index - self.next > JUMP_FRAMES:
            self._start(index)
        img = None
        while self.next <= index:
            img = self._read()
            if img is None:
                break
        if img is not None:
            self.last = img
        elif self.last is None:
            self._start(max(0, index - 1))   # past the end on a fresh reader: the frame before it
            self.last = self._read()
            if self.last is None:
                raise RecipeError(f"{self.path.name}: ffmpeg decoded no frame at {index * self.duration / self.frames:.2f}s")
        return self.last

    def close(self) -> None:
        """Stop the decoder, if one is running. Tested through frame() restarting; the atexit call is not tested."""
        if self.proc:
            self.proc.kill()
            self.proc.wait()
            self.proc = None


@register_layer("video")
class VideoLayer(Layer):
    """Fills the canvas with a 360 video's frame for each movie frame. Tested by test_video.py."""

    OPTIONS = {"path": None, "credit": None}

    def __init__(self, spec, ctx, index):
        """Validate that a path is given. Tested by test_video.py::test_find_video_needs_exactly_one."""
        super().__init__(spec, ctx, index)
        if not self.opts["path"]:
            raise RecipeError(f"video layer #{index + 1} needs a path, such as \"data/*\"")
        self.reader: VideoReader | None = None

    @classmethod
    def frames_for(cls, spec: dict, recipe, fps: int) -> int:
        """Return how many frames make a movie as long as the video at fps, for [animation] frames = 0. Tested by test_video.py::test_build_a_360_video."""
        return max(1, round(probe(find_video(recipe, spec.get("path") or ""))["duration"] * fps))

    def inputs(self) -> dict:
        """Fingerprint the video by name, size and modification time, so a new or changed clip is decoded again. Tested by test_video.py::test_build_a_360_video."""
        p = find_video(self.ctx.recipe, self.opts["path"])
        return {"file": f"{p.name}:{p.stat().st_size}:{int(p.stat().st_mtime)}"}

    def prepare(self) -> None:
        """Probe the video and refuse one that isn't the 2:1 frame of a stitched 360 video, storing what the workers need. Tested by test_video.py::test_build_a_360_video and test_refuses_a_flat_video."""
        path = find_video(self.ctx.recipe, self.opts["path"])
        info = probe(path)
        if abs(info["width"] / info["height"] - 2) > RATIO_TOLERANCE:
            raise RecipeError(f"{path.name} is {info['width']}x{info['height']}, not the 2:1 frame of a stitched 360 video: "
                              "export the 360 (equirectangular) version from your camera's app")
        self.store.put_json("video", {"path": str(path), **info})

    def attach(self) -> None:
        """Read the stored description; the decoder starts on the first frame this process renders. Tested by test_video.py::test_build_a_360_video."""
        self.info = self.store.get_json("video")

    def render(self, canvas: np.ndarray, t) -> None:
        """Replace the canvas with the video's frame for this movie frame, in linear light. Tested by test_video.py::test_build_a_360_video."""
        if self.reader is None:
            self.reader = VideoReader(Path(self.info["path"]), self.info["duration"], t.count, self.ctx.width,
                                      self.ctx.height)
        canvas[...] = _LINEAR[self.reader.frame(t.index)]

    def checks(self) -> list[Check]:
        """Report the clip's size and length and that it is 2:1. Tested by test_video.py::test_build_a_360_video."""
        i = self.store.get_json("video")
        ok = abs(i["width"] / i["height"] - 2) <= RATIO_TOLERANCE
        return [Check(f"video #{self.index + 1} is a 2:1 360 frame", ok,
                      f"{i['width']}x{i['height']} {i['codec']}, {i['duration']:.1f}s")]

    def credits(self) -> list[str]:
        """Return the recipe's credit for the footage, when it gives one. Tested by test_video.py::test_build_a_360_video."""
        return [self.opts["credit"]] if self.opts["credit"] else []
