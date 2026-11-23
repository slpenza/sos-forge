"""Tests for the subsolar point."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from sosforge.sun import subsolar_point


def test_equinox_solstice_and_noon():
    """Checks declination at the 2024 equinox and solstices, and the equation-of-time offset of the subsolar longitude at noon UTC. Covers subsolar_point; this test is not itself tested."""
    utc = timezone.utc
    assert subsolar_point(datetime(2024, 3, 20, 3, 6, tzinfo=utc))[0] == pytest.approx(0.0, abs=0.05)
    assert subsolar_point(datetime(2024, 6, 20, 20, 51, tzinfo=utc))[0] == pytest.approx(23.44, abs=0.03)
    assert subsolar_point(datetime(2024, 12, 21, 9, 20, tzinfo=utc))[0] == pytest.approx(-23.44, abs=0.03)
    assert subsolar_point(datetime(2024, 4, 15, 12, 0, tzinfo=utc))[1] == pytest.approx(0.0, abs=0.5)
    assert subsolar_point(datetime(2024, 11, 3, 12, 0, tzinfo=utc))[1] == pytest.approx(-4.1, abs=0.3)
    assert subsolar_point(datetime(2024, 11, 3, 12, 0)) == subsolar_point(datetime(2024, 11, 3, 12, 0, tzinfo=utc))
