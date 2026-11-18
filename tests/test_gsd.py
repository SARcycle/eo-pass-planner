"""Ground sample distance: registry lookup, off-nadir scaling and the --max-gsd filter."""

import json
import math
from datetime import datetime, timezone
from pathlib import Path

import pytest

from eo_pass_planner import find_passes, load_registry, select
from eo_pass_planner.geometry import off_nadir_gsd
from eo_pass_planner.registry import classify

FAMILIES = load_registry()
RECORDS = json.loads((Path(__file__).parent / "fixture_gp.json").read_text())


def spherical_geometry(off_nadir_deg, altitude_km, radius_km=6371.0):
    """Slant range and incidence for a given off-nadir angle over a spherical Earth."""
    th = math.radians(off_nadir_deg)
    inc = math.asin((radius_km + altitude_km) / radius_km * math.sin(th))
    slant = radius_km * math.sin(inc - th) / math.sin(th)
    return slant, math.degrees(inc)


def test_worldview3_published_off_nadir_gsd():
    # Published WorldView-3 figures: 0.31 m at nadir, 0.34 m at 20 deg off-nadir.
    slant, inc = spherical_geometry(20.0, 617.0)
    along, cross = off_nadir_gsd(0.31, slant, 617.0, inc)
    assert math.sqrt(along * cross) == pytest.approx(0.34, abs=0.005)
    assert along < cross


def test_nadir_gsd_is_unchanged():
    assert off_nadir_gsd(0.5, 600.0, 600.0, 0.0) == (0.5, 0.5)


@pytest.mark.parametrize("name, gsd", [
    ("WORLDVIEW-1 (WV-1)", 0.50), ("WORLDVIEW-3 (WV-3)", 0.31), ("PLEIADES NEO 3", 0.30),
    ("SKYSAT-C12", 0.57), ("KOMPSAT-3A", 0.55), ("SUPERVIEW NEO-3 01", None), ("ICEYE-X40", None),
])
def test_registry_nadir_gsd(name, gsd):
    assert classify(name, FAMILIES).nadir_gsd(name) == gsd


def test_superview_neo2_is_sar():
    assert classify("SUPERVIEW NEO-2 03", FAMILIES).type == "sar"
    assert classify("SUPERVIEW NEO-1 01", FAMILIES).type == "optical"


def worldview3_passes(**kw):
    rec = dict(next(r for r in RECORDS if r["OBJECT_NAME"] == "ICEYE-X40"))
    rec["OBJECT_NAME"] = "WORLDVIEW-3 (WV-3)"        # similar orbit
    cands = select([rec], FAMILIES)
    return find_passes(cands, 46.948, 7.447, start=datetime(2026, 11, 12, tzinfo=timezone.utc),
                       end=datetime(2026, 11, 15, tzinfo=timezone.utc), min_sun_elevation=-90,
                       **kw)


def test_max_gsd_filter():
    everything = worldview3_passes(include_infeasible=True)
    assert all(p.gsd >= 0.31 and p.gsd_cross >= p.gsd_along for p in everything)
    sharp = worldview3_passes(max_gsd=0.40)
    assert sharp and all(p.gsd <= 0.40 for p in sharp)
    rejected = [p for p in worldview3_passes(max_gsd=0.40, include_infeasible=True)
                if not p.feasible]
    assert rejected and all("GSD " in p.reason and "> 0.4 m" in p.reason for p in rejected)


def test_max_gsd_unknown_for_sar():
    cands = select(RECORDS, FAMILIES, name="ICEYE-X40")
    passes = find_passes(cands, 46.948, 7.447, start=datetime(2026, 11, 12, tzinfo=timezone.utc),
                         end=datetime(2026, 11, 13, tzinfo=timezone.utc), max_gsd=1.0,
                         include_infeasible=True)
    assert passes and all("GSD unknown" in p.reason for p in passes)
