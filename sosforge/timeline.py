"""Frame timing: maps a frame index to a loop/linear fraction and, optionally, a calendar date."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone


def parse_date(value) -> datetime | None:
    """Parse 'YYYY-MM-DD', 'today' or a date into a UTC datetime, returning None for empty input. Tested by test_timeline.py::test_linear_dates_and_loop_fractions."""
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    if str(value).lower() == "today":
        now = datetime.now(timezone.utc)
        return datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
    parsed = datetime.fromisoformat(str(value))
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def fractional_year(when: datetime) -> float:
    """Return a date as a fractional year, counting the time of day and the real length of that year. Tested by test_timeline.py::test_fractional_year."""
    start = datetime(when.year, 1, 1, tzinfo=when.tzinfo)
    length = datetime(when.year + 1, 1, 1, tzinfo=when.tzinfo) - start
    return when.year + (when - start) / length


@dataclass(frozen=True)
class FrameTime:
    """The time state of one frame that layers and HUD templates read. Tested by test_timeline.py::test_linear_dates_and_loop_fractions."""

    index: int
    count: int
    fraction: float
    date: datetime | None

    def values(self) -> dict:
        """Return template values for the HUD: frame, frames, fraction, date, and year when the timeline has dates. Tested by test_timeline.py::test_linear_dates_and_loop_fractions and test_fractional_year."""
        v = {"frame": self.index, "frames": self.count, "fraction": self.fraction, "date": self.date}
        if self.date is not None:
            v["year"] = self.date.year
        return v


class Timeline:
    """Frame-to-time mapping: loops never repeat frame 0, linear runs end exactly on the final state. Tested by test_timeline.py."""

    def __init__(self, frames: int, fps: int, loop: bool, start=None, end=None):
        """Store frame count, rate, loop mode and an optional date range. Tested by test_timeline.py::test_linear_dates_and_loop_fractions."""
        if frames < 1:
            raise ValueError("frames must be >= 1")
        self.frames, self.fps, self.loop = frames, fps, loop
        self.start, self.end = parse_date(start), parse_date(end)
        if (self.start is None) != (self.end is None):
            raise ValueError("animation.start and animation.end must be given together")
        if self.start and self.end <= self.start:
            raise ValueError("animation.end must be after animation.start")

    def at(self, index: int) -> FrameTime:
        """Return the FrameTime for a frame index, where a single-frame linear timeline sits at its end. Tested by test_timeline.py::test_linear_dates_and_loop_fractions."""
        if self.loop:
            fraction = index / self.frames
        else:
            fraction = 1.0 if self.frames == 1 else index / (self.frames - 1)
        when = None
        if self.start:
            when = self.start + timedelta(seconds=(self.end - self.start).total_seconds() * fraction)
        return FrameTime(index, self.frames, fraction, when)
