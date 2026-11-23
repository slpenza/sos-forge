"""Tests for ffmpeg command construction."""
from __future__ import annotations

from pathlib import Path

import pytest

from sosforge.encode import default_bitrate, ffmpeg_command, validate_video


def test_ffmpeg_commands():
    """Checks the H.264 argv carries every SOS-required flag and colour tags, and MPEG-4 honours the 25 Mbps floor. Covers ffmpeg_command and default_bitrate; this test is not itself tested."""
    s = " ".join(ffmpeg_command("ffmpeg", 4096, 2048, 30, Path("o.mp4")))
    for flag in ("-c:v libx264", "-pix_fmt yuv420p", "-crf 18", "-preset slow", "-movflags +faststart", "-bf 0",
                 "-profile:v main", "-s:v 4096x2048", "-framerate 30", "color_trc=bt709"):
        assert flag in s, flag
    m = " ".join(ffmpeg_command("ffmpeg", 4096, 2048, 30, Path("o.mp4"), codec="mpeg4"))
    assert "-c:v mpeg4" in m and "-b:v 50M" in m and "-bf 0" in m
    assert default_bitrate(1024, 512) == "25M"
    with pytest.raises(ValueError, match="25M"):
        ffmpeg_command("ffmpeg", 4096, 2048, 30, Path("o.mp4"), codec="mpeg4", bitrate="10M")


def test_validate_video_per_codec():
    """Checks validation names carry the codec, H.264 adds the BT.709 tag check, and mismatches fail. Covers validate_video; this test is not itself tested."""
    info = {"codec": "mpeg4", "profile": "Simple Profile", "width": 256, "height": 128, "frames": 6, "fps": 30.0,
            "pix_fmt": "yuv420p", "color": "", "frame_types": {"I": 1, "P": 5}}
    m4 = validate_video(info, 256, 128, 6, 30, "mpeg4")
    assert all(c.passed for c in m4) and all("[mpeg4]" in c.name for c in m4) and len(m4) == 6
    h = validate_video({**info, "codec": "h264", "color": "tv, bt709, progressive"}, 256, 128, 6, 30, "h264")
    assert len(h) == 7 and all(c.passed for c in h)
    bad = validate_video({**info, "codec": "h264", "color": "tv, bt709/unknown/unknown", "frame_types": {"B": 2}},
                         256, 128, 6, 30, "h264")
    assert [c.name for c in bad if not c.passed] == ["video [h264] has no B-frames", "video [h264] BT.709 limited-range tags"]
