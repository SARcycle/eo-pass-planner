"""Command line interface: ``eo-pass-planner``."""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from datetime import datetime, timedelta, timezone

from .catalog import (MAX_AGE_HOURS, cache_age_hours, fetch_gp, records_from_tle_file,
                      refresh_selected, select)
from .geometry import find_passes
from .registry import load_registry

EPILOG = """\
examples:
  # Which ICEYE satellite images Bern around 10:30 UTC on 20 Nov, and with what heading?
  eo-pass-planner --name ICEYE --lat 46.948 --lon 7.447 --time 2026-11-20T10:30Z --window 1h

  # Same, rank by a known incidence angle (sanity check)
  eo-pass-planner --name ICEYE --lat 46.948 --lon 7.447 --time 2026-11-20T10:30Z --incidence 28

  # Optical: rank by an expected off-nadir angle
  eo-pass-planner --family Pleiades --lat 46.948 --lon 7.447 --time 2026-11-20T10:30Z --window 3h \
      --off-nadir 12 --onad-tol 3

  # All SAR satellites from Japan over the next 24 h
  eo-pass-planner --type sar --country japan --lat 35.68 --lon 139.69 --start now --end +24h

  # Just list the satellites a filter selects
  eo-pass-planner --type optical --country France --list
"""


def parse_duration(s: str) -> timedelta:
    m = re.fullmatch(r"\s*([\d.]+)\s*([smhd]?)\s*", s)
    if not m:
        raise argparse.ArgumentTypeError(f"bad duration {s!r} (use e.g. 90s, 30m, 2h, 1d)")
    val, unit = float(m.group(1)), m.group(2) or "m"
    return timedelta(**{{"s": "seconds", "m": "minutes", "h": "hours", "d": "days"}[unit]: val})


def parse_time(s: str, base: datetime | None = None) -> datetime:
    """ISO 8601 (naive = UTC), 'now', or '+<duration>' relative to ``base``/now."""
    now = datetime.now(timezone.utc)
    if s.lower() == "now":
        return now
    if s.startswith("+"):
        return (base or now) + parse_duration(s[1:])
    t = datetime.fromisoformat(s.replace("Z", "+00:00").replace("z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="eo-pass-planner",
        description="Predict satellite passes over a location and report the acquisition "
                    "geometry (heading, ASC/DESC, look side, incidence, ...).",
        epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)

    g = p.add_argument_group("satellite selection (criteria are combined with AND; regex, case-insensitive)")
    g.add_argument("--name", help="satellite name, e.g. ICEYE, 'ICEYE-X(3|4)\\d', SENTINEL-1A")
    g.add_argument("--family", help="registry family, e.g. ICEYE, Capella, Sentinel-1")
    g.add_argument("--type", choices=["sar", "optical"], help="sensor type (from registry)")
    g.add_argument("--country", help="country (from registry), e.g. Finland, USA, Japan")
    g.add_argument("--operator", help="operator (from registry), e.g. Airbus, Planet")
    g.add_argument("--norad", type=int, nargs="+", help="NORAD catalogue number(s)")
    g.add_argument("--list", action="store_true", help="only list matching satellites and exit")

    g = p.add_argument_group("target and time")
    g.add_argument("--lat", type=float, help="target latitude (deg, WGS84)")
    g.add_argument("--lon", type=float, help="target longitude (deg, WGS84)")
    g.add_argument("--height", type=float, default=0.0, help="target ellipsoidal height in m (default 0)")
    g.add_argument("--time", help="approximate acquisition time, ISO 8601 (UTC if no zone)")
    g.add_argument("--window", type=parse_duration, default=timedelta(minutes=30),
                   help="search +/- this around --time (default 30m)")
    g.add_argument("--start", help="window start instead of --time/--window ('now', ISO, '+2h')")
    g.add_argument("--end", help="window end ('+24h' = relative to start)")

    g = p.add_argument_group("geometry constraints (override the registry)")
    g.add_argument("--incidence", type=float, help="expected incidence angle; ranks passes by match")
    g.add_argument("--inc-tol", type=float, help="drop passes whose incidence differs more than this")
    g.add_argument("--inc-min", type=float)
    g.add_argument("--inc-max", type=float)
    g.add_argument("--off-nadir", type=float,
                   help="expected off-nadir angle (optical); ranks passes by match")
    g.add_argument("--onad-tol", type=float,
                   help="drop passes whose off-nadir angle differs more than this")
    g.add_argument("--off-nadir-min", type=float)
    g.add_argument("--off-nadir-max", type=float)
    g.add_argument("--look-side", choices=["left", "right", "both"])
    g.add_argument("--min-sun", type=float, help="minimum sun elevation at target (deg)")
    g.add_argument("--all", action="store_true", help="also show passes outside the sensor envelope")

    g = p.add_argument_group("data and output")
    g.add_argument("--tle-file", help="use a local TLE/3LE file instead of CelesTrak")
    g.add_argument("--group", default="active", help="CelesTrak group (default: active)")
    g.add_argument("--refresh", action="store_true", help="force re-download of orbital elements")
    g.add_argument("--registry", help="custom registry YAML")
    g.add_argument("--step", type=float, default=20.0, help="search step in seconds (default 20)")
    g.add_argument("--csv", help="write results to this CSV file")
    g.add_argument("--json", action="store_true", help="print results as JSON")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    families = load_registry(args.registry)

    if args.tle_file:
        records, source = records_from_tle_file(args.tle_file), args.tle_file
    else:
        records, source = fetch_gp(args.group, refresh=args.refresh)

    def _select(recs):
        return select(recs, families, name=args.name, family=args.family, sat_type=args.type,
                      country=args.country, operator=args.operator, norad=args.norad)

    cands = _select(records)
    if not args.tle_file and cache_age_hours(source) >= MAX_AGE_HOURS:
        # Full download failed: fetch only the selected satellites.
        records, how = refresh_selected(records, cands, name=args.name, refresh=args.refresh)
        if how:
            cands = _select(records)
            source = f"{source} + CelesTrak {how}"
            print(f"         fetched current elements for the selection instead ({how})",
                  file=sys.stderr)
        elif how is None:
            print("         to refresh just your satellites, select them with a plain --name "
                  "(e.g. ICEYE) or narrow the selection to <= 10 satellites", file=sys.stderr)
        else:
            print("         the selected satellites could not be fetched individually either",
                  file=sys.stderr)
    if not cands:
        print("No satellites match the selection.", file=sys.stderr)
        return 1

    if args.list:
        print(f"{len(cands)} satellites (source: {source})")
        print(f"{'NORAD':>6}  {'name':<32} {'family':<24} {'type':<8} {'country':<16} {'incl':>6}  epoch")
        for c in sorted(cands, key=lambda c: c.name):
            print(f"{c.norad:>6}  {c.name:<32} {c.family.name:<24} {c.family.type:<8} "
                  f"{c.family.country:<16} {c.satellite.model.inclo * 57.29578:6.2f}  "
                  f"{c.satellite.epoch:%Y-%m-%d %H:%M}")
        return 0

    if args.inc_tol is not None and args.incidence is None:
        print("--inc-tol needs --incidence.", file=sys.stderr)
        return 2
    if args.onad_tol is not None and args.off_nadir is None:
        print("--onad-tol needs --off-nadir.", file=sys.stderr)
        return 2
    if args.lat is None or args.lon is None:
        print("--lat and --lon are required (or use --list).", file=sys.stderr)
        return 2
    if args.start:
        start = parse_time(args.start)
        end = parse_time(args.end, base=start) if args.end else start + timedelta(days=1)
        ref = parse_time(args.time) if args.time else None
    elif args.time:
        ref = parse_time(args.time)
        start, end = ref - args.window, ref + args.window
    else:
        print("Give --time (with optional --window) or --start/--end.", file=sys.stderr)
        return 2

    inc_rng = None
    if args.inc_min is not None or args.inc_max is not None:
        inc_rng = (args.inc_min if args.inc_min is not None else 0.0,
                   args.inc_max if args.inc_max is not None else 90.0)
    onr = None
    if args.off_nadir_min is not None or args.off_nadir_max is not None:
        onr = (args.off_nadir_min if args.off_nadir_min is not None else 0.0,
               args.off_nadir_max if args.off_nadir_max is not None else 90.0)

    passes = find_passes(cands, args.lat, args.lon, start=start, end=end, ref_time=ref,
                         height_m=args.height, step_s=args.step, incidence_range=inc_rng,
                         off_nadir_range=onr, min_sun_elevation=args.min_sun,
                         look_side=args.look_side, include_infeasible=args.all)

    if args.incidence is not None:
        for p in passes:
            p.incidence_delta = p.incidence - args.incidence
        if args.inc_tol is not None:
            passes = [p for p in passes if abs(p.incidence_delta) <= args.inc_tol]
    if args.off_nadir is not None:
        for p in passes:
            p.off_nadir_delta = p.off_nadir - args.off_nadir
        if args.onad_tol is not None:
            passes = [p for p in passes if abs(p.off_nadir_delta) <= args.onad_tol]
    if args.incidence is not None:
        passes.sort(key=lambda p: (not p.feasible, abs(p.incidence_delta)))
    elif args.off_nadir is not None:
        passes.sort(key=lambda p: (not p.feasible, abs(p.off_nadir_delta)))
    else:
        passes.sort(key=lambda p: (not p.feasible, abs(p.offset_min)))

    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8") as fh:
            rows = [p.as_dict() for p in passes]
            w = csv.DictWriter(fh, fieldnames=list(rows[0]) if rows else ["satellite"])
            w.writeheader()
            w.writerows(rows)
    if args.json:
        print(json.dumps([p.as_dict() for p in passes], indent=2))
        return 0

    print(f"Target {args.lat:.5f}, {args.lon:.5f} | window {start:%Y-%m-%d %H:%M} - "
          f"{end:%Y-%m-%d %H:%M} UTC | {len(cands)} satellites checked | source: {source}")
    if not passes:
        print("No passes found. Widen --window, relax constraints, or use --all.")
        return 0
    hdr = (f"{'satellite':<24} {'time (UTC)':<19} {'dt[min]':>8} {'pass':<4} {'look':<5} "
           f"{'hdg_trk':>7} {'hdg_scn':>7} {'look_az':>7} {'inc':>5} {'offnad':>6} "
           f"{'grd_km':>6} {'sun':>5} {'LST':>5} {'TLEage':>6}")
    if args.incidence is not None:
        hdr += f" {'dInc':>5}"
    if args.off_nadir is not None:
        hdr += f" {'dOnad':>5}"
    if args.all:
        hdr += "  note"
    print(hdr)
    for p in passes:
        line = (f"{p.satellite[:24]:<24} {p.time_utc:%Y-%m-%d %H:%M:%S} {p.offset_min:8.1f} "
                f"{p.direction:<4} {p.look_side:<5} {p.heading_track:7.2f} {p.heading_scene:7.2f} "
                f"{p.look_azimuth:7.2f} {p.incidence:5.1f} {p.off_nadir:6.1f} "
                f"{p.ground_range_km:6.0f} {p.sun_elevation:5.1f} {p.local_solar_time:>5} "
                f"{p.tle_age_days:5.1f}d")
        if args.incidence is not None:
            line += f" {p.incidence_delta:+5.1f}"
        if args.off_nadir is not None:
            line += f" {p.off_nadir_delta:+5.1f}"
        if args.all:
            line += "  " + ("ok" if p.feasible else p.reason)
        print(line)
    old = [p for p in passes if abs(p.tle_age_days) > 7]
    if old:
        print(f"\nnote: {len(old)} pass(es) are predicted >7 days from the element epoch; "
              "timing may be off by minutes and manoeuvres are not modelled. Heading is robust.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
