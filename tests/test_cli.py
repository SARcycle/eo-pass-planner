"""Command-line options, run offline against the fixture."""

import json
import shutil
from pathlib import Path

import pytest

from eo_pass_planner import catalog
from eo_pass_planner.cli import main

FIXTURE = Path(__file__).parent / "fixture_gp.json"
WHERE = ["--name", "ICEYE", "--lat", "46.948", "--lon", "7.447",
         "--start", "2026-11-12T00:00Z", "--end", "+3d"]


@pytest.fixture(autouse=True)
def fresh_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    shutil.copy(FIXTURE, catalog.cache_dir() / "gp_active.json")


def run_json(capsys, *extra):
    assert main([*WHERE, "--json", *extra]) == 0
    return json.loads(capsys.readouterr().out)


def test_off_nadir_ranks_and_filters(capsys):
    passes = run_json(capsys, "--off-nadir", "25", "--onad-tol", "5")
    assert passes
    deltas = [abs(p["off_nadir_delta"]) for p in passes]
    assert deltas == sorted(deltas)
    assert all(d <= 5 for d in deltas)
    for p in passes:
        assert p["off_nadir_delta"] == pytest.approx(p["off_nadir"] - 25)


def test_off_nadir_range(capsys):
    passes = run_json(capsys, "--off-nadir-min", "20", "--off-nadir-max", "30")
    assert passes
    assert all(20 <= p["off_nadir"] <= 30 for p in passes)
    everything = run_json(capsys)
    assert any(p["off_nadir"] < 20 for p in everything)     # the minimum really removed some


def test_table_shows_off_nadir_delta(capsys):
    assert main([*WHERE, "--off-nadir", "25"]) == 0
    assert "dOnad" in capsys.readouterr().out


@pytest.mark.parametrize("args", [["--onad-tol", "3"], ["--inc-tol", "3"]])
def test_tolerance_needs_target(capsys, args):
    assert main([*WHERE, *args]) == 2
    assert "needs" in capsys.readouterr().err


def test_max_gsd_drops_satellites_without_gsd(capsys):
    assert main([*WHERE, "--max-gsd", "1"]) == 1
    assert "No selected satellite has a known nadir GSD <= 1 m" in capsys.readouterr().err


def test_list_shows_gsd_column(capsys):
    assert main(["--name", "ICEYE", "--list"]) == 0
    assert "gsd0" in capsys.readouterr().out
