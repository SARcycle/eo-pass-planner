"""Download, cache and filter orbital elements (CelesTrak GP data, OMM/JSON)."""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .orbit import Satellite, read_tle_file
from .registry import UNKNOWN, Family, classify, flexible_pattern, normalize_name

CELESTRAK_GP = "https://celestrak.org/NORAD/elements/gp.php"
USER_AGENT = "eo-pass-planner/0.1 (+https://celestrak.org usage policy respected: cached)"
MAX_AGE_HOURS = 6.0
MAX_SINGLE_QUERIES = 10       # max. number of per-satellite requests
_NOT_A_PAYLOAD = re.compile(r"\b(DEB|R/B|AKM|PKM|COOLANT)\b")


def cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    d = Path(base) / "eo-pass-planner"
    d.mkdir(parents=True, exist_ok=True)
    return d


def cache_age_hours(path: Path) -> float:
    return (time.time() - path.stat().st_mtime) / 3600.0


def _describe(exc: Exception) -> str:
    """Short reason for a failed download."""
    if isinstance(exc, urllib.error.HTTPError):
        try:
            body = " ".join(exc.read(400).decode("utf-8", "replace").split())
        except Exception:  # noqa: BLE001
            body = ""
        return f"HTTP {exc.code}: {body}" if body else f"HTTP {exc.code} {exc.reason}"
    if isinstance(exc, urllib.error.URLError):
        return str(exc.reason)
    return str(exc)


def _download(query: dict, path: Path, *, max_age_hours: float, refresh: bool,
              allow_empty: bool = False, quiet: bool = False) -> list[dict]:
    """Fetch one CelesTrak query, cached at ``path``; fall back to a stale cache."""
    fresh = path.exists() and cache_age_hours(path) < max_age_hours
    if refresh or not fresh:
        url = f"{CELESTRAK_GP}?{urllib.parse.urlencode({**query, 'FORMAT': 'json'})}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=120) as r:
                raw = r.read()
            try:
                records = json.loads(raw)
            except ValueError:
                records = None
            if records is None and allow_empty and b"No GP data" in raw:
                records, raw = [], b"[]"
            if not isinstance(records, list) or not (records or allow_empty):
                raise ValueError(f"unexpected response: {raw[:200]!r}")
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(raw)
            tmp.replace(path)
        except Exception as exc:  # noqa: BLE001 - fall back to cache
            what = "&".join(f"{k}={v}" for k, v in query.items())
            if not path.exists():
                raise RuntimeError(f"Could not download CelesTrak {what}: {_describe(exc)}") from exc
            if not quiet:
                when = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
                print(f"warning: CelesTrak download of {what} failed ({_describe(exc)})\n"
                      f"         using the cached copy from {when:%Y-%m-%d %H:%M} UTC "
                      f"({cache_age_hours(path) / 24:.1f} days old)", file=sys.stderr)
    return json.loads(path.read_text(encoding="utf-8"))


def fetch_gp(group: str = "active", max_age_hours: float = MAX_AGE_HOURS,
             refresh: bool = False) -> tuple[list[dict], Path]:
    """Return CelesTrak GP records for ``group``, using a local cache.

    CelesTrak does not serve unchanged data twice, so the cache is reused while
    younger than ``max_age_hours``. If the download fails, a stale cache is used.
    """
    path = cache_dir() / f"gp_{group}.json"
    return _download({"GROUP": group}, path, max_age_hours=max_age_hours, refresh=refresh), path


def refresh_selected(records: list[dict], candidates: list[Candidate], name: str | None = None,
                     max_age_hours: float = MAX_AGE_HOURS,
                     refresh: bool = False) -> tuple[list[dict], str | None]:
    """Update just the selected satellites with small CelesTrak queries.

    Used when the full catalogue could not be refreshed. Returns the merged
    records and a description of what was fetched: ``None`` if the selection
    is too broad, ``""`` if nothing could be fetched.
    """
    if name and re.fullmatch(r"[A-Za-z0-9 _/-]+", name):
        queries = [{"NAME": name.strip()}]
    elif len(candidates) <= MAX_SINGLE_QUERIES:
        queries = [{"CATNR": c.norad} for c in candidates]
    else:
        return records, None

    fetched: list[dict] = []
    for q in queries:
        key, val = next(iter(q.items()))
        slug = re.sub(r"[^A-Za-z0-9_-]+", "_", str(val)).upper()
        try:
            fetched += _download(q, cache_dir() / f"gp_{key.lower()}_{slug}.json",
                                 max_age_hours=max_age_hours, refresh=refresh,
                                 allow_empty=True, quiet=True)
        except RuntimeError:
            continue
    if not fetched:
        return records, ""

    by_id = {int(r["NORAD_CAT_ID"]): r for r in records}
    for r in fetched:
        old = by_id.get(int(r["NORAD_CAT_ID"]))
        if old is None and _NOT_A_PAYLOAD.search(r.get("OBJECT_NAME", "")):
            continue            # skip debris and rocket bodies
        if old is None or r.get("EPOCH", "") >= old.get("EPOCH", ""):
            by_id[int(r["NORAD_CAT_ID"])] = r
    how = (f"NAME={queries[0]['NAME']}" if "NAME" in queries[0]
           else f"{len(queries)} CATNR queries")
    return list(by_id.values()), how


def records_from_tle_file(path: str | Path) -> list[dict]:
    """Read a 2LE/3LE text file (e.g. from Space-Track) into minimal records."""
    out = []
    for sat in read_tle_file(path):
        out.append({"_satellite": sat, "OBJECT_NAME": sat.name or str(sat.model.satnum),
                    "NORAD_CAT_ID": sat.model.satnum,
                    "OBJECT_ID": _cospar(getattr(sat.model, "intldesg", ""))})
    return out


def _cospar(intldesg: str) -> str:
    """TLE designator such as 14016A -> COSPAR id 2014-016A."""
    intldesg = (intldesg or "").strip()
    if len(intldesg) < 5 or not intldesg[:2].isdigit():
        return ""
    yy = int(intldesg[:2])
    return f"{1900 + yy if yy >= 57 else 2000 + yy}-{intldesg[2:]}"


@dataclass
class Candidate:
    satellite: Satellite
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
    regular expressions (a plain word works as a substring match). In ``name``,
    dashes, underscores and spaces are interchangeable.
    """
    rx = lambda s: re.compile(s, re.IGNORECASE) if s else None  # noqa: E731
    name_rx = flexible_pattern(name) if name else None
    fam_rx, country_rx, op_rx = rx(family), rx(country), rx(operator)
    # Filters on registry metadata only make sense for registered satellites.
    needs_registry = registered_only or any([family, sat_type, country, operator])

    out = []
    for rec in records:
        obj = rec.get("OBJECT_NAME", "")
        fam = classify(obj, families, norad=int(rec["NORAD_CAT_ID"]), cospar=rec.get("OBJECT_ID"))
        if needs_registry and fam is UNKNOWN:
            continue
        if norad and int(rec["NORAD_CAT_ID"]) not in norad:
            continue
        if name_rx and not name_rx.search(normalize_name(obj)):
            continue
        if fam_rx and not fam_rx.search(fam.name):
            continue
        if sat_type and fam.type != sat_type.lower():
            continue
        if country_rx and not country_rx.search(fam.country):
            continue
        if op_rx and not op_rx.search(fam.operator):
            continue
        sat = rec.get("_satellite") or Satellite.from_omm(rec)
        out.append(Candidate(sat, obj.strip(), int(rec["NORAD_CAT_ID"]), fam))
    return out
