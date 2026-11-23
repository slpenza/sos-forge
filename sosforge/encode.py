"""ffmpeg encoding for SOS movies (H.264 Main/no B-frames, or NOAA-documented MPEG-4 Part 2) and decode-back validation."""
from __future__ import annotations

import collections
import re
import shutil
import subprocess
from pathlib import Path

from .checks import Check

COLOR_FILTER = ("scale=out_color_matrix=bt709:out_range=tv:flags=accurate_rnd+full_chroma_int,format=yuv420p,"
                "setparams=range=tv:color_primaries=bt709:color_trc=bt709:colorspace=bt709")


def find_ffmpeg() -> str:
    """Return ffmpeg from PATH, falling back to the imageio-ffmpeg static binary. Tested indirectly by test_build.py::test_movie_build_end_to_end."""
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:  # noqa: BLE001 - any failure means not available
        raise RuntimeError("ffmpeg not found: install ffmpeg or `pip install imageio-ffmpeg`") from exc


def default_bitrate(width: int, height: int) -> str:
    """Return the MPEG-4 Part 2 bitrate, never below NOAA's 25 Mbps minimum and 50 Mbps at 4096x2048. Tested by test_encode.py::test_ffmpeg_commands."""
    return f"{max(25, round(50 * width * height / (4096 * 2048)))}M"


def ffmpeg_command(ffmpeg: str, width: int, height: int, fps: int, output: Path, codec: str = "h264", crf: int = 18,
                   preset: str = "slow", bitrate: str | None = None) -> list[str]:
    """Build the argv that reads raw RGB24 on stdin and writes an SOS-compatible MP4 without B-frames, refusing MPEG-4 targets under 25 Mbps. Tested by test_encode.py::test_ffmpeg_commands."""
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "info", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
           "-s:v", f"{width}x{height}", "-framerate", str(fps), "-i", "pipe:0", "-an", "-vf", COLOR_FILTER]
    if codec == "h264":
        cmd += ["-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-profile:v", "main", "-bf", "0",
                "-g", str(fps), "-keyint_min", str(fps), "-sc_threshold", "0", "-tag:v", "avc1"]
    elif codec == "mpeg4":
        br = bitrate or default_bitrate(width, height)
        mbps = float(br.rstrip("Mm"))
        if mbps < 25:
            raise ValueError(f"mpeg4 bitrate {br} is below NOAA's recommended minimum of 25M")
        cmd += ["-c:v", "mpeg4", "-b:v", br, "-maxrate", f"{mbps * 1.25:g}M", "-bufsize", f"{mbps * 2:g}M",
                "-bf", "0", "-g", str(fps), "-mbd", "rd", "-flags", "+mv4+aic", "-trellis", "2", "-tag:v", "mp4v"]
    else:
        raise ValueError(f"unknown codec {codec!r}")
    return cmd + ["-pix_fmt", "yuv420p", "-movflags", "+faststart", str(output)]


def probe_video(path: Path, ffmpeg: str | None = None) -> dict:
    """Decode an MP4 through ffmpeg showinfo and return codec, profile, size, fps, colour tags, frame count and frame types. Tested by test_build.py::test_movie_build_end_to_end."""
    res = subprocess.run([ffmpeg or find_ffmpeg(), "-hide_banner", "-i", str(path), "-map", "0:v:0", "-vf", "showinfo",
                          "-f", "null", "-"], capture_output=True, text=True, check=False)
    head = res.stderr.split("Stream mapping:")[0]
    info: dict = {"path": str(path), "bytes": Path(path).stat().st_size}
    m = re.search(r"Stream #0:0\S*: Video: (\w+)(?: \(([^)]*)\))?.*?, (\w+)(?:\(([^)]*)\))?, (\d+)x(\d+).*?, "
                  r"([\d.]+) fps", head)
    if m:
        info.update(codec=m.group(1), profile=m.group(2), pix_fmt=m.group(3), color=m.group(4) or "",
                    width=int(m.group(5)), height=int(m.group(6)), fps=float(m.group(7)))
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+).*?bitrate: (\d+) kb/s", head)
    if m:
        info["duration_s"] = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
        info["bitrate_kbps"] = int(m.group(4))
    types = re.findall(r"\bn:\s*\d+ .*?\btype:([IPB])", res.stderr)
    info["frames"], info["frame_types"] = len(types), dict(collections.Counter(types))
    return info


def validate_video(info: dict, width: int, height: int, frames: int, fps: int, codec: str) -> list[Check]:
    """Check a probed MP4 for codec, 2:1 size, frame count, rate, yuv420p, no B-frames and BT.709 tags for H.264. Tested by test_build.py::test_movie_build_end_to_end and test_encode.py::test_validate_video_per_codec."""
    v = f"video [{codec}]"
    checks = [
        Check(f"{v} codec", info.get("codec") == codec, f"{info.get('codec')} ({info.get('profile')})"),
        Check(f"{v} 2:1 resolution", (info.get("width"), info.get("height")) == (width, height),
              f"{info.get('width')}x{info.get('height')}"),
        Check(f"{v} frame count", info.get("frames") == frames, f"{info.get('frames')} decoded / {frames}"),
        Check(f"{v} frame rate", abs(info.get("fps", 0) - fps) < 0.01, f"{info.get('fps')} fps"),
        Check(f"{v} pixel format yuv420p", info.get("pix_fmt") == "yuv420p", str(info.get("pix_fmt"))),
        Check(f"{v} has no B-frames", info["frame_types"].get("B", 0) == 0, str(info["frame_types"])),
    ]
    if codec == "h264":
        c = info.get("color", "")
        checks.append(Check(f"{v} BT.709 limited-range tags", c.startswith("tv, bt709") and "unknown" not in c,
                            c or "untagged"))
    return checks
