"""Download, cache and filter orbital elements (CelesTrak GP data, OMM/JSON)."""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from skyfield.api import EarthSatellite, load

from .registry import Family, classify

CELESTRAK_URL = "https://celestrak.org/NORAD/elements/gp.php?GROUP={group}&FORMAT=json"
USER_AGENT = "eo-pass-planner/0.1 (+https://celestrak.org usage policy respected: cached)"


def cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    d = Path(base) / "eo-pass-planner"
    d.mkdir(parents=True, exist_ok=True)
    return d


def fetch_gp(group: str = "active", max_age_hours: float = 6.0,
             refresh: bool = False) -> tuple[list[dict], Path]:
    """Return CelesTrak GP records for ``group``, using a local cache.

    CelesTrak asks clients not to re-download unchanged data more often than
    every couple of hours, so the cache is reused while younger than
    ``max_age_hours``. If the download fails, a stale cache is used.
    """
    path = cache_dir() / f"gp_{group}.json"
    fresh = path.exists() and (time.time() - path.stat().st_mtime) < max_age_hours * 3600
    if refresh or not fresh:
        try:
            req = urllib.request.Request(CELESTRAK_URL.format(group=group),
                                         headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=120) as r:
                raw = r.read()
            records = json.loads(raw)
            if not isinstance(records, list) or not records:
                raise ValueError(f"unexpected response: {raw[:200]!r}")
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(raw)
            tmp.replace(path)
        except Exception as exc:  # noqa: BLE001 - fall back to cache
            if not path.exists():
                raise RuntimeError(f"Could not download CelesTrak data: {exc}") from exc
            print(f"warning: download failed ({exc}); using cached {path}", file=sys.stderr)
    return json.loads(path.read_text()), path


def records_from_tle_file(path: str | Path) -> list[dict]:
    """Read a 2LE/3LE text file (e.g. from Space-Track) into minimal records."""
    ts = load.timescale()
    out = []
    for sat in load.tle_file(str(path), ts=ts, reload=False):
        out.append({"_satellite": sat, "OBJECT_NAME": sat.name or str(sat.model.satnum),
                    "NORAD_CAT_ID": sat.model.satnum})
    return out


@dataclass
class Candidate:
    satellite: EarthSatellite
    name: str
    norad: int
    family: Family


def select(records: list[dict], families: list[Family], *,
           name: str | None = None, family: str | None = None,
           sat_type: str | None = None, country: str | None = None,
           operator: str | None = None, norad: list[int] | None = None,
           registered_only: bool = False) -> list[Candidate]:
    """Filter catalogue records. All given criteria must match (AND).

    ``name``, ``family``, ``country`` and ``operator`` are case-insensitive
    regular expressions (a plain word works as a substring match).
    """
    ts = load.timescale()
    rx = lambda s: re.compile(s, re.IGNORECASE) if s else None  # noqa: E731
    name_rx, fam_rx, country_rx, op_rx = rx(name), rx(family), rx(country), rx(operator)
    # Filters on registry metadata only make sense for registered satellites.
    needs_registry = registered_only or any([family, sat_type, country, operator])

    out = []
    for rec in records:
        obj = rec.get("OBJECT_NAME", "")
        fam = classify(obj, families)
        is_registered = fam.pattern.pattern != "$^"
        if needs_registry and not is_registered:
            continue
        if norad and int(rec["NORAD_CAT_ID"]) not in norad:
            continue
        if name_rx and not name_rx.search(obj):
            continue
        if fam_rx and not fam_rx.search(fam.name):
            continue
        if sat_type and fam.type != sat_type.lower():
            continue
        if country_rx and not country_rx.search(fam.country):
            continue
        if op_rx and not op_rx.search(fam.operator):
            continue
        sat = rec.get("_satellite") or EarthSatellite.from_omm(ts, rec)
        out.append(Candidate(sat, obj.strip(), int(rec["NORAD_CAT_ID"]), fam))
    return out
