# eo-pass-planner

Predict which Earth-observation satellites pass over a location in a given time
window, and get the acquisition geometry for each pass: heading,
ascending/descending, look side, incidence and off-nadir angle, sun elevation.

It is meant for **planned (future) acquisitions**, where no product metadata
exists yet. You need only a satellite name, type, operator or country, the
target coordinates and an approximate time.

## Install / run (uv)

```bash
cd eo-pass-planner
uv sync
uv run eo-pass-planner --help
```

## Run without installing (OSGeo4W, locked-down machines)

The tool needs only Python ≥ 3.10 with **numpy** and **PyYAML**, which the
OSGeo4W Shell (and QGIS's Python) already has. Nothing has to be installed:

```bat
cd path\to\eo-pass-planner
python eo-pass-planner.py --help
python eo-pass-planner.py --name ICEYE --lat 46.948 --lon 7.447 --time 2026-11-14T10:30Z
```

`python -m eo_pass_planner ...` also works from inside the `src` folder.

SGP4 comes from the [`sgp4`](https://pypi.org/project/sgp4/) package when it is
installed (fast C++ core). Otherwise the pure-Python copy bundled in
`src/eo_pass_planner/_vendor/` is used. It gives identical results but is a few
times slower, which only matters for broad selections over long windows.

On Windows the cache is `C:\Users\<you>\.cache\eo-pass-planner\`. If a proxy
blocks the CelesTrak download, use `--tle-file` with elements you fetched
yourself.

Orbital elements are downloaded from [CelesTrak](https://celestrak.org) (GP
data, `active` group, JSON/OMM format, so catalogue numbers above 99999 work).
They are cached for 6 h in `~/.cache/eo-pass-planner/`, in line with CelesTrak's
usage policy. Use `--refresh` to force a download, or `--tle-file` to supply your
own elements (e.g. from Space-Track) for offline or historical runs.

## Examples

```bash
# Which ICEYE satellite can image Bern around 10:30 UTC, and with what heading?
uv run eo-pass-planner --name ICEYE --lat 46.948 --lon 7.447 --time 2026-11-14T10:30Z --window 1h

# Rank the candidates by a known/expected incidence angle (sanity check)
uv run eo-pass-planner --name ICEYE --lat 46.948 --lon 7.447 --time 2026-11-14T10:30Z --incidence 28

# Keep only candidates within +/-3 deg of that incidence
uv run eo-pass-planner --name ICEYE --lat 46.948 --lon 7.447 --time 2026-11-14T10:30Z --incidence 28 --inc-tol 3

# All Japanese SAR satellites over the next 24 h
uv run eo-pass-planner --type sar --country japan --lat 46.948 --lon 7.447 --start now --end +24h

# Just list which satellites a filter selects (name, family, type, country, inclination, epoch)
uv run eo-pass-planner --type optical --country France --list

# CSV / JSON output
uv run eo-pass-planner --name ICEYE --lat 46.948 --lon 7.447 --time 2026-11-14T10:30Z --csv passes.csv
uv run eo-pass-planner --name ICEYE --lat 46.948 --lon 7.447 --time 2026-11-14T10:30Z --json
```

Selection criteria (`--name`, `--family`, `--type`, `--country`, `--operator`,
`--norad`) are combined with AND. Each one is a case-insensitive regular expression.
`--name` works for any satellite in the catalogue. `--type`, `--country`,
`--operator` and `--family` rely on the registry (see below).

## How it works

1. Every selected satellite is propagated with SGP4 over the time window and
   rotated into the Earth-fixed ITRS frame (GMST1982, polar motion neglected,
   UT1 ≈ UTC, as Skyfield does by default up to the UT1 approximation).
2. For each pass the tool solves for the instant at which the line of sight
   satellite → target is perpendicular to the Earth-fixed velocity,
   `(T − r)·v = 0`. For SAR this is the **zero-Doppler time** of the target. For
   any sensor it is also the closest approach, when the target is broadside.
3. The geometry at that instant is checked against the sensor envelope from the
   registry. Each family has a look side (left, right or both), an incidence
   range (SAR), an off-nadir range (optical) and a minimum sun elevation
   (optical). Passes outside the envelope are dropped. `--all` shows them,
   together with the reason they were rejected.

## Output columns

| column | meaning |
|---|---|
| `time (UTC)` | zero-Doppler / closest-approach time (sub-second precision in CSV/JSON) |
| `dt[min]` | offset from `--time` |
| `pass` | `ASC` (moving north) / `DESC` (moving south) |
| `look` | side of the ground track the target is on, relative to flight direction |
| `hdg_trk` | **ground-track heading** at the sub-satellite point, degrees clockwise from north. This is what is usually called "platform heading" in SAR metadata. |
| `hdg_scn` | the flight (azimuth) direction expressed in the target's local north/east frame, i.e. the image azimuth direction *at the scene*. It differs from `hdg_trk` by meridian convergence (a few degrees for typical SAR ground ranges, more at high latitude). |
| `look_az` | azimuth of the line of sight satellite → target, at the target (≈ `hdg_scn` ± 90°) |
| `inc` | incidence angle at the target relative to the WGS84 ellipsoid normal |
| `offnad` | off-nadir (look) angle at the satellite |
| `grd_km` | ground distance sub-satellite point → target |
| `sun` | sun elevation at the target (deg) |
| `LST` | local mean solar time at the target |
| `TLEage` | time between the orbital-element epoch and the predicted pass |
| `dInc` | predicted minus expected incidence (only with `--incidence`) |

The CSV/JSON output also contains slant range, altitude, sub-satellite point,
inclination and the element epoch.

## Accuracy

* **Heading** depends almost only on inclination, latitude and pass direction.
  It is accurate to well below 1° even weeks ahead.
* **Pass timing and which satellite** degrade with prediction horizon. SGP4 with
  fresh elements is good to roughly a kilometre (a fraction of a second) for a
  day or two. Beyond about a week, along-track errors can reach minutes, and
  operators (including ICEYE) manoeuvre their satellites. Rerun shortly before
  the acquisition and watch the `TLEage` column.
* Constellations are not homogeneous. ICEYE, for example, has satellites in
  ~97.5° (sun-synchronous), ~53° and ~45° orbits, and the heading over the same
  site differs by tens of degrees between them.
* Check `hdg_trk` vs `hdg_scn` against one real product of the mission you use
  to confirm which convention its metadata follows.

## Registry

[`src/eo_pass_planner/registry.yaml`](src/eo_pass_planner/registry.yaml) maps name
patterns to sensor type, operator, country, look side and viewing envelope.
The angle envelopes are approximate public figures. Override them per run
(`--inc-min/--inc-max`, `--off-nadir-max`, `--look-side`, `--min-sun`), or pass
your own file with `--registry`. Satellites not in the registry can still be
selected by `--name`/`--norad`. They are treated as type `unknown` with a
generous 0–60° off-nadir envelope.

## Python API

```python
from datetime import datetime, timezone
from eo_pass_planner import fetch_gp, load_registry, select, find_passes

records, _ = fetch_gp()
cands = select(records, load_registry(), name="ICEYE")
passes = find_passes(cands, 46.948, 7.447,
                     start=datetime(2026, 11, 14, 9, 30, tzinfo=timezone.utc),
                     end=datetime(2026, 11, 14, 11, 30, tzinfo=timezone.utc))
for p in passes:
    print(p.satellite, p.time_utc, p.direction, p.look_side, round(p.heading_track, 2))
```

## Tests

```bash
uv run pytest
```

The tests run offline against a frozen CelesTrak snapshot in `tests/fixture_gp.json`.
They also check that the frame maths matches Skyfield (a dev-only dependency) and
that the bundled pure-Python SGP4 matches the installed one.
