"""Offline tests using a frozen CelesTrak snapshot (tests/fixture_gp.json, epoch ~2026-11-11)."""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from eo_pass_planner import find_passes, load_registry, select, sun_elevation

BERN = (46.948, 7.447)
RECORDS = json.loads((Path(__file__).parent / "fixture_gp.json").read_text())
FAMILIES = load_registry()


def _passes(**sel):
    cands = select(RECORDS, FAMILIES, **sel)
    return find_passes(cands, *BERN,
                       start=datetime(2026, 11, 12, tzinfo=timezone.utc),
                       end=datetime(2026, 11, 15, tzinfo=timezone.utc))


def test_selection_by_type_and_country():
    assert {c.name for c in select(RECORDS, FAMILIES, country="finland")} == {"ICEYE-X40", "ICEYE-X84"}
    assert {c.name for c in select(RECORDS, FAMILIES, family="sentinel-1")} == {"SENTINEL-1A"}
    assert select(RECORDS, FAMILIES, sat_type="optical") == []


def test_sentinel1_headings_over_bern():
    passes = _passes(name="SENTINEL-1A")
    assert passes, "expected feasible Sentinel-1A passes"
    for p in passes:
        assert p.look_side == "RIGHT"               # S1 is right-looking only
        assert 19 <= p.incidence <= 47
        if p.direction == "ASC":
            assert p.heading_track == pytest.approx(345.6, abs=1.0)
        else:
            assert p.heading_track == pytest.approx(194.4, abs=1.0)
        # dawn-dusk orbit: ~06:00 DESC, ~18:00 ASC local time
        hour = int(p.local_solar_time[:2])
        assert (5 <= hour <= 7) if p.direction == "DESC" else (16 <= hour <= 19)


def test_zero_doppler_is_perpendicular():
    from skyfield.api import load, wgs84
    from skyfield.framelib import itrs

    p = _passes(name="ICEYE-X40")[0]
    sat = select(RECORDS, FAMILIES, name="ICEYE-X40")[0].satellite
    t = load.timescale().from_datetime(p.time_utc)
    r, v = sat.at(t).frame_xyz_and_velocity(itrs)
    los = wgs84.latlon(*BERN).itrs_xyz.km - r.km
    vv = v.km_per_s
    cos = (los @ vv) / ((los @ los) ** 0.5 * (vv @ vv) ** 0.5)
    assert abs(cos) < 1e-5


def test_mid_inclination_iceye_heading_differs():
    # ICEYE-X84 is in a ~53 deg orbit: its heading at 47N is far from the polar ~194/346
    passes = _passes(name="ICEYE-X84")
    assert passes
    for p in passes:
        assert 20 < p.heading_track < 160 or 200 < p.heading_track < 340


def test_sun_elevation_reference():
    # Bern near solar noon at the June solstice: 90 - 46.95 + 23.44 = 66.5 deg
    noon = datetime(2026, 6, 21, 11, 30, tzinfo=timezone.utc)
    assert sun_elevation(noon, *BERN) == pytest.approx(66.4, abs=0.5)
