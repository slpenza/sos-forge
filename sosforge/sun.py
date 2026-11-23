"""Where the Sun is overhead at a moment in time (the subsolar point), accurate to about 0.01 degrees for 1950-2050.

Low-precision solar coordinates from the Astronomical Almanac: mean longitude and anomaly give the ecliptic longitude,
which gives declination (the subsolar latitude) and right ascension; the subsolar longitude is right ascension minus
Greenwich mean sidereal time.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone


def subsolar_point(when: datetime) -> tuple[float, float]:
    """Return the subsolar latitude and longitude in degrees for a UTC datetime (naive times are taken as UTC). Tested by test_sun.py::test_equinox_solstice_and_noon."""
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    n = when.timestamp() / 86400.0 + 2440587.5 - 2451545.0
    mean_lon = (280.460 + 0.9856474 * n) % 360.0
    g = math.radians((357.528 + 0.9856003 * n) % 360.0)
    lam = math.radians(mean_lon + 1.915 * math.sin(g) + 0.020 * math.sin(2 * g))
    eps = math.radians(23.439 - 0.0000004 * n)
    dec = math.degrees(math.asin(math.sin(eps) * math.sin(lam)))
    ra = math.degrees(math.atan2(math.cos(eps) * math.sin(lam), math.cos(lam)))
    gmst = (280.46061837 + 360.98564736629 * n) % 360.0
    lon = (ra - gmst + 180.0) % 360.0 - 180.0
    return dec, lon
