"""Tests for playlist.sos and README generation."""
from __future__ import annotations

from conftest import write_recipe
from sosforge.build import size_label
from sosforge.package import length_label, playlist_text, readme_text
from sosforge.recipe import load_recipe

TOML = '''[dataset]
name = "Lunar Phases (29.5-Day Synodic Cycle)"
description = """High-resolution   Lunar
Reconnaissance Orbiter visualization."""
output = "movie"
[playlist]
tiltx = 6.68
animate = 1
category = "Astronomy"
majorcategory = "Astronomy"
bogus = 1
[[layers]]
type = "basemap"
color = "#000000"
'''


def test_length_label():
    """Checks movie lengths read as seconds, whole minutes, or minutes and seconds. Covers length_label; this test is not itself tested."""
    assert [length_label(s) for s in (20, 40, 60, 90, 120, 72)] == ["20 s", "40 s", "1 min", "1 min 30 s", "2 min", "1 min 12 s"]


def test_playlist_and_readme(tmp_path):
    """Checks the playlist has a single-line {{ }} description with credits, fps for movies and verbatim extras, and the README carries deploy path, licences and checks. Covers playlist_text, readme_text, _table and size_label; this test is not itself tested."""
    r = load_recipe(write_recipe(tmp_path / "lunar", TOML))
    assert r.warnings == ["[playlist] 'bogus' is not a documented SOS playlist key"]
    lines = playlist_text(r, "moon_phases_4k.mp4", ["NASA SVS"]).splitlines()
    assert lines[0] == "name = Lunar Phases (29.5-Day Synodic Cycle)"
    assert lines[1] == "description = {{ High-resolution Lunar Reconnaissance Orbiter visualization. Credits: NASA SVS. }}"
    for want in ("data = moon_phases_4k.mp4", "fps = 30", "tiltx = 6.68", "animate = 1", "category = Astronomy",
                 "majorcategory = Astronomy"):
        assert want in lines
    summary = {"width": 4096, "height": 2048, "video": {"bytes": 9}, "settings": {"a": 1},
               "checks": [{"name": "x", "passed": True, "detail": "d"}]}
    md = readme_text(r, "moon_phases_4k.mp4", "ab" * 32, summary, ["NASA SVS"], ["CC BY-SA 4.0"], "lunar_phases")
    assert "/shared/sos/media/site-custom/lunar_phases/" in md and "| x | PASS | d |" in md and "- CC BY-SA 4.0" in md
    assert size_label(4096) == "4k" and size_label(1000) == "1000x500"
    assert playlist_text(r, "m_h264.mp4", [], "h264").splitlines()[0] == "name = Lunar Phases (29.5-Day Synodic Cycle) (H.264)"
    assert "label = labels.txt\nlabelvisible = false" in playlist_text(r, "m.mp4", [], labels=True)
    assert "label" not in playlist_text(r, "m.mp4", [])
    vmd = readme_text(r, "m_h264.mp4", "00", {**summary, "video": {"bytes": 1, "codec": "h264"}}, [], [], "lunar_phases_h264",
                      "h264", ("mpeg4", "lunar_phases"))
    assert "H.264 variant" in vmd and "`lunar_phases/` (MPEG-4 Part 2)" in vmd
