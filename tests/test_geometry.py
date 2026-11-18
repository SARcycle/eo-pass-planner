"""Offline tests using a frozen CelesTrak snapshot (tests/fixture_gp.json, epoch ~2026-11-11)."""

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

from eo_pass_planner import find_passes, load_registry, select, sun_elevation
from eo_pass_planner.orbit import geodetic_to_itrs, itrs_to_geodetic, jd_from_datetime

BERN = (46.948, 7.447)
FIXTURE = Path(__file__).parent / "fixture_gp.json"
RECORDS = json.loads(FIXTURE.read_text())
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
    p = _passes(name="ICEYE-X40")[0]
    sat = select(RECORDS, FAMILIES, name="ICEYE-X40")[0].satellite
    r, v = sat.state(*jd_from_datetime(p.time_utc))
    los = geodetic_to_itrs(*BERN) - r
    cos = (los @ v) / ((los @ los) ** 0.5 * (v @ v) ** 0.5)
    assert abs(cos) < 1e-5


def test_altitude_is_orbit_height():
    for p in _passes(name="ICEYE-X40"):
        assert 450 < p.altitude_km < 700


def test_matches_skyfield():
    """The frame and geodetic maths match Skyfield."""
    sf = pytest.importorskip("skyfield.api")
    from skyfield.framelib import itrs

    rec = next(r for r in RECORDS if r["OBJECT_NAME"] == "SENTINEL-1A")
    ours = select([rec], FAMILIES)[0].satellite
    ts = sf.load.timescale()
    theirs = sf.EarthSatellite.from_omm(ts, rec)
    for hour in range(0, 72, 7):
        when = datetime(2026, 11, 12, tzinfo=timezone.utc) + timedelta(hours=hour, seconds=13.7)
        r, v = ours.state(*jd_from_datetime(when))
        pos, vel = theirs.at(ts.from_datetime(when)).frame_xyz_and_velocity(itrs)
        assert np.linalg.norm(r - pos.km) < 0.1                 # km
        assert np.linalg.norm(v - vel.km_per_s) < 1e-4          # km/s
        lat, lon, _ = itrs_to_geodetic(r)
        sub = sf.wgs84.subpoint_of(theirs.at(ts.from_datetime(when)))
        assert lat == pytest.approx(sub.latitude.degrees, abs=1e-3)
        assert lon == pytest.approx(sub.longitude.degrees, abs=1e-3)


def test_vendored_sgp4_matches_installed():
    """The bundled sgp4 gives the same results as the installed one."""
    code = (
        "import json, sys; from datetime import datetime, timezone;"
        "from eo_pass_planner import select, find_passes, load_registry;"
        "recs = json.load(open(sys.argv[1]));"
        "c = select(recs, load_registry(), name='ICEYE-X40');"
        "ps = find_passes(c, 46.948, 7.447, start=datetime(2026, 11, 12, tzinfo=timezone.utc),"
        " end=datetime(2026, 11, 13, tzinfo=timezone.utc));"
        "import sgp4; print(json.dumps({'path': sgp4.__file__, 'inc': [p.incidence for p in ps]}))"
    )
    env = dict(os.environ, EO_PASS_PLANNER_VENDORED_SGP4="1")
    out = subprocess.run([sys.executable, "-c", code, str(FIXTURE)], env=env,
                         capture_output=True, text=True, check=True).stdout
    res = json.loads(out)
    assert "_vendor" in res["path"]
    ref = [p.incidence for p in find_passes(select(RECORDS, FAMILIES, name="ICEYE-X40"), *BERN,
           start=datetime(2026, 11, 12, tzinfo=timezone.utc),
           end=datetime(2026, 11, 13, tzinfo=timezone.utc))]
    assert res["inc"] == pytest.approx(ref, abs=1e-9)


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
