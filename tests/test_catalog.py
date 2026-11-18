"""CelesTrak download fallbacks, with the network replaced by a fake."""

import io
import json
import os
import time
import urllib.error
import urllib.parse
from pathlib import Path

import pytest

from eo_pass_planner import catalog, load_registry, select
from eo_pass_planner.cli import main

RECORDS = json.loads((Path(__file__).parent / "fixture_gp.json").read_text())
REFUSED = (b"GP data has not updated since your last successful\n"
           b"download of GROUP=active at 2026-11-18 10:35:38 UTC.\nData is updated once every 2 hours.")


@pytest.fixture
def stale_cache(tmp_path, monkeypatch):
    """A week-old cache of the full catalogue."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    path = catalog.cache_dir() / "gp_active.json"
    path.write_text(json.dumps(RECORDS))
    week_ago = time.time() - 7 * 86400
    os.utime(path, (week_ago, week_ago))
    return path


def fake_celestrak(monkeypatch, newer: dict[str, list[dict]]):
    """Fake CelesTrak: refuse the full catalogue, answer smaller queries from ``newer``."""
    calls = []

    def urlopen(req, timeout=None):
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(req.full_url).query))
        calls.append(q)
        if "GROUP" in q:
            raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, io.BytesIO(REFUSED))
        key = q.get("NAME") or q.get("CATNR")
        body = json.dumps(newer[key]).encode() if key in newer else b"No GP data found"
        return io.BytesIO(body)

    monkeypatch.setattr(catalog.urllib.request, "urlopen", urlopen)
    return calls


def bumped(name):
    rec = dict(next(r for r in RECORDS if r["OBJECT_NAME"] == name))
    rec["EPOCH"] = "2026-11-18T06:00:00.000000"
    return rec


def test_403_message_and_stale_fallback(stale_cache, monkeypatch, capsys):
    fake_celestrak(monkeypatch, {})
    records, path = catalog.fetch_gp()
    assert len(records) == len(RECORDS) and path == stale_cache
    err = capsys.readouterr().err
    assert "HTTP 403: GP data has not updated" in err
    assert "7.0 days old" in err


def test_name_query_refreshes_selection(stale_cache, monkeypatch):
    debris = dict(bumped("ICEYE-X40"), OBJECT_NAME="ICEYE-X40 DEB", NORAD_CAT_ID=99999)
    calls = fake_celestrak(monkeypatch, {"ICEYE": [bumped("ICEYE-X40"), bumped("ICEYE-X84"), debris]})
    cands = select(RECORDS, load_registry(), name="ICEYE")
    records, how = catalog.refresh_selected(RECORDS, cands, name="ICEYE")
    assert how == "NAME=ICEYE"
    assert calls == [{"NAME": "ICEYE", "FORMAT": "json"}]
    new = select(records, load_registry(), name="ICEYE")
    assert {c.satellite.epoch.day for c in new} == {18}
    assert {c.name for c in new} == {"ICEYE-X40", "ICEYE-X84"}          # debris not merged in
    # A second run inside the cache period does not hit CelesTrak again.
    catalog.refresh_selected(RECORDS, cands, name="ICEYE")
    assert len(calls) == 1


def test_regex_name_falls_back_to_catnr(stale_cache, monkeypatch):
    calls = fake_celestrak(monkeypatch, {"60549": [bumped("ICEYE-X40")]})
    cands = select(RECORDS, load_registry(), name="ICEYE-X(40|84)")
    records, how = catalog.refresh_selected(RECORDS, cands, name="ICEYE-X(40|84)")
    assert how == "2 CATNR queries"
    assert sorted(c["CATNR"] for c in calls) == ["101122", "60549"]
    epochs = {c.name: c.satellite.epoch.day for c in select(records, load_registry(), name="ICEYE")}
    assert epochs == {"ICEYE-X40": 18, "ICEYE-X84": 11}     # not returned: kept as is


def test_broad_selection_is_not_split(stale_cache, monkeypatch):
    calls = fake_celestrak(monkeypatch, {})
    monkeypatch.setattr(catalog, "MAX_SINGLE_QUERIES", 1)
    cands = select(RECORDS, load_registry(), sat_type="sar")
    assert catalog.refresh_selected(RECORDS, cands) == (RECORDS, None)
    assert calls == []


def test_cli_reports_targeted_refresh(stale_cache, monkeypatch, capsys):
    fake_celestrak(monkeypatch, {"ICEYE": [bumped("ICEYE-X40")]})
    assert main(["--name", "ICEYE", "--list"]) == 0
    out, err = capsys.readouterr()
    assert "HTTP 403" in err and "fetched current elements for the selection instead (NAME=ICEYE)" in err
    assert "2026-11-18 06:00" in out
