"""SGP4 propagation and Earth-fixed coordinates using only numpy and sgp4.

Follows Skyfield's defaults: SGP4 is evaluated at UTC and TEME is rotated to
the Earth-fixed frame by GMST, without polar motion. UT1 is taken as UTC,
which costs well under a kilometre.

Times are UTC Julian dates split into whole part and fraction, as sgp4 expects.
"""

from __future__ import annotations

import math
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

_VENDOR = str(Path(__file__).parent / "_vendor")
if os.environ.get("EO_PASS_PLANNER_VENDORED_SGP4"):    # force the bundled copy
    sys.path.insert(0, _VENDOR)
try:
    from sgp4 import omm
    from sgp4.api import Satrec
except ImportError:                                     # not installed: use the bundled copy
    sys.path.append(_VENDOR)
    from sgp4 import omm
    from sgp4.api import Satrec

# WGS84 ellipsoid (km)
A_KM = 6378.137
F = 1.0 / 298.257223563
E2 = F * (2.0 - F)

_UNIX_JD = 2440587.5
_DAY_S = 86400.0
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


# ---- time ------------------------------------------------------------------

def jd_from_datetime(t: datetime) -> tuple[float, float]:
    """UTC datetime -> (whole Julian date, fraction of day)."""
    secs = (t.astimezone(timezone.utc) - _EPOCH) / timedelta(seconds=1)
    days, rem = divmod(secs, _DAY_S)
    return _UNIX_JD + days, rem / _DAY_S


def datetime_from_jd(jd: float, fr: float) -> datetime:
    return _EPOCH + timedelta(days=(jd - _UNIX_JD)) + timedelta(days=float(fr))


# ---- frames ----------------------------------------------------------------

def _gmst1982(jd, fr):
    """GMST angle (rad) and its rate (rad/s)."""
    t = (jd - 2451545.0 + fr) / 36525.0
    g = 67310.54841 + (8640184.812866 + (0.093104 + (-6.2e-6) * t) * t) * t
    dg = 8640184.812866 + (0.093104 * 2.0 + (-6.2e-6 * 3.0) * t) * t
    theta = (jd % 1.0 + fr + g / _DAY_S % 1.0) % 1.0 * 2.0 * math.pi
    theta_dot = (1.0 + dg / (_DAY_S * 36525.0)) * 2.0 * math.pi / _DAY_S
    return theta, theta_dot


def teme_to_itrs(jd, fr, r, v):
    """Rotate TEME position/velocity (3,) or (3, N) into the Earth-fixed frame."""
    theta, w = _gmst1982(jd, fr)
    c, s = np.cos(theta), np.sin(theta)
    x = c * r[0] + s * r[1]
    y = -s * r[0] + c * r[1]
    vx = c * v[0] + s * v[1] + w * y
    vy = -s * v[0] + c * v[1] - w * x
    return np.array([x, y, r[2]]), np.array([vx, vy, v[2]])


def geodetic_to_itrs(lat_deg: float, lon_deg: float, height_km: float = 0.0) -> np.ndarray:
    la, lo = math.radians(lat_deg), math.radians(lon_deg)
    n = A_KM / math.sqrt(1.0 - E2 * math.sin(la) ** 2)
    return np.array([(n + height_km) * math.cos(la) * math.cos(lo),
                     (n + height_km) * math.cos(la) * math.sin(lo),
                     (n * (1.0 - E2) + height_km) * math.sin(la)])


def itrs_to_geodetic(r) -> tuple[float, float, float]:
    """Earth-fixed position (km) -> geodetic latitude, longitude (deg), height (km)."""
    x, y, z = (float(c) for c in r)
    lon = math.atan2(y, x)
    p = math.hypot(x, y)
    lat = math.atan2(z, p * (1.0 - E2))
    h = 0.0
    for _ in range(6):
        n = A_KM / math.sqrt(1.0 - E2 * math.sin(lat) ** 2)
        h = p / math.cos(lat) - n
        lat = math.atan2(z, p * (1.0 - E2 * n / (n + h)))
    return math.degrees(lat), math.degrees(lon), h


# ---- satellites ------------------------------------------------------------

class Satellite:
    """An SGP4 element set with an Earth-fixed state function."""

    def __init__(self, model, name: str | None = None):
        self.model = model
        self.name = name

    @classmethod
    def from_omm(cls, fields: dict) -> Satellite:
        sat = Satrec()
        omm.initialize(sat, fields)
        return cls(sat, fields.get("OBJECT_NAME"))

    @classmethod
    def from_tle(cls, line1: str, line2: str, name: str | None = None) -> Satellite:
        return cls(Satrec.twoline2rv(line1, line2), name)

    @property
    def epoch(self) -> datetime:
        return datetime_from_jd(self.model.jdsatepoch, self.model.jdsatepochF)

    def state(self, jd, fr):
        """Earth-fixed position (km) and velocity (km/s) at UTC (jd, fr).

        Arrays of times give arrays of vectors. Failed propagations are NaN.
        """
        if np.ndim(fr):
            fr = np.asarray(fr, dtype=float)
            jd = np.broadcast_to(np.asarray(jd, dtype=float), fr.shape)
            err, r, v = self.model.sgp4_array(np.ascontiguousarray(jd), fr)
            r, v = np.array(r, dtype=float).T, np.array(v, dtype=float).T
            bad = np.asarray(err) != 0
            r[:, bad] = np.nan
            v[:, bad] = np.nan
        else:
            err, r, v = self.model.sgp4(float(jd), float(fr))
            r, v = np.array(r, dtype=float), np.array(v, dtype=float)
            if err:
                r[:] = v[:] = np.nan
        return teme_to_itrs(jd, fr, r, v)


def read_tle_file(path: str | Path) -> list[Satellite]:
    """Parse a TLE text file, with or without name lines."""
    lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    sats, name, i = [], None, 0
    while i < len(lines):
        line = lines[i].rstrip()
        if line.startswith("1 ") and i + 1 < len(lines) and lines[i + 1].startswith("2 "):
            sats.append(Satellite.from_tle(line, lines[i + 1].rstrip(), name))
            name, i = None, i + 2
            continue
        name = line[2:].strip() if line.startswith("0 ") else (line.strip() or None)
        i += 1
    return sats
