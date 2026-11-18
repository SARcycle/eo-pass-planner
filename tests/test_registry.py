"""Registry: name matching, ids, bands and merging of user files."""

import pytest

from eo_pass_planner import load_registry
from eo_pass_planner.catalog import _cospar
from eo_pass_planner.registry import UNKNOWN, classify, user_registry_path

FAMILIES = load_registry()


@pytest.mark.parametrize("name", ["SENTINEL-2A", "SENTINEL 2A", "SENTINEL2A", "Sentinel_2A"])
def test_separators_are_interchangeable(name):
    assert classify(name, FAMILIES).name == "Sentinel-2"


def test_per_satellite_bands():
    wv = classify("WORLDVIEW-3 (WV-3)", FAMILIES)
    assert wv.nadir_bands("WORLDVIEW-3 (WV-3)")["swir"] == 3.7
    assert wv.nadir_gsd("WORLDVIEW-3 (WV-3)") == 0.31                     # finest band
    assert wv.nadir_gsd("WORLDVIEW-3 (WV-3)", ["vis", "swir"]) == 3.7      # coarsest requested
    assert wv.nadir_gsd("WORLDVIEW-1 (WV-1)", ["vis"]) is None             # pan only
    assert classify("SKYSAT C12", FAMILIES).nadir_gsd("SKYSAT C12") == 0.65
    assert classify("SKYSAT C17", FAMILIES).nadir_gsd("SKYSAT C17", ["vis"]) == 0.72


def write_user(tmp_path, monkeypatch, text):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    path = user_registry_path()
    path.parent.mkdir(parents=True)
    path.write_text(text)
    return path


def test_user_file_merges(tmp_path, monkeypatch):
    path = write_user(tmp_path, monkeypatch, """
families:
  WorldView:                 # change one field, keep the rest
    off_nadir: [0, 30]
  Landsat: null              # remove
  WV3-only:                  # new family, checked before the bundled ones
    pattern: '^WORLDVIEW-3'
    type: optical
    gsd: 0.3
  My-sat:
    norad: [99001]
    cospar: ['2030-001']
    type: optical
    bands: {pan: 1.0, swir: 8}
""")
    fams = load_registry()
    by_name = {f.name: f for f in fams}
    assert by_name["WorldView"].off_nadir == (0, 30)
    assert by_name["WorldView"].type == "optical"
    assert by_name["WorldView"].source == f"bundled + {path}"
    assert "Landsat" not in by_name
    assert classify("WORLDVIEW-3 (WV-3)", fams).name == "WV3-only"
    assert classify("WORLDVIEW-2 (WV-2)", fams).name == "WorldView"
    assert classify("WHATEVER", fams, norad=99001).name == "My-sat"
    assert classify("WHATEVER", fams, cospar="2030-001B").name == "My-sat"
    assert classify("WHATEVER", fams, norad=1) is UNKNOWN
    assert load_registry(user=False)[0].source == "bundled"


def test_unknown_band_group_is_rejected(tmp_path, monkeypatch):
    write_user(tmp_path, monkeypatch, "families:\n  X:\n    pattern: X\n    bands: {red: 1}\n")
    with pytest.raises(ValueError, match="unknown band group"):
        load_registry()


def test_cospar_from_tle_designator():
    assert _cospar("14016A") == "2014-016A"
    assert _cospar("98067A  ") == "1998-067A"
    assert _cospar("") == ""
