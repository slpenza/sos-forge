"""Tests for frame timing."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from sosforge.timeline import Timeline, parse_date


def test_linear_dates_and_loop_fractions():
    """Checks loops never repeat frame 0, linear runs end on the end date, single frames sit at the end, and bad ranges raise. Covers Timeline, FrameTime and parse_date; this test is not itself tested."""
    loop = Timeline(720, 30, True)
    assert loop.at(0).fraction == 0 and loop.at(719).fraction == pytest.approx(719 / 720)
    lin = Timeline(5, 30, False, "2020-01-01", "2020-01-05")
    assert lin.at(0).date == datetime(2020, 1, 1, tzinfo=timezone.utc)
    assert lin.at(4).date == datetime(2020, 1, 5, tzinfo=timezone.utc) and lin.at(2).fraction == 0.5
    assert Timeline(1, 30, False).at(0).fraction == 1.0
    assert set(lin.at(1).values()) == {"frame", "frames", "fraction", "date"}
    assert parse_date("today").tzinfo is not None and parse_date("") is None
    with pytest.raises(ValueError):
        Timeline(5, 30, False, "2020-01-01", None)
    with pytest.raises(ValueError):
        Timeline(5, 30, False, "2021-01-01", "2020-01-01")
