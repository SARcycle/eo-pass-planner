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

On Windows the cache is `C:\Users\<you>\.cache\eo-pass-planner\`.

On company networks Python may not get through to CelesTrak. Then either set
the proxy before running the tool (`set HTTPS_PROXY=http://<proxy>:<port>`), or
open <https://celestrak.org/NORAD/elements/gp.php?GROUP=active&FORMAT=json> in
a browser, save it, and pass the file with `--tle-file`. This also works
offline and never downloads anything.

Orbital elements are downloaded from [CelesTrak](https://celestrak.org) (GP
data, `active` group, JSON/OMM format, so catalogue numbers above 99999 work).
They are cached for 6 h in `~/.cache/eo-pass-planner/`, in line with CelesTrak's
usage policy. CelesTrak serves each query only once per data update (about every
2 h) to the same IP address and answers HTTP 403 otherwise, e.g. when a
colleague behind the same company network already downloaded it. The tool then
uses the cached catalogue and fetches just the selected satellites with a small
`NAME=` query (if `--name` is a plain word such as `ICEYE`) or up to 10 `CATNR=`
queries. Use `--refresh` to force a download, or `--tle-file` to supply your
own elements (e.g. from Space-Track) for offline or historical runs.

## Examples

```bash
# Which ICEYE satellite can image Bern around 10:30 UTC, and with what heading?
uv run eo-pass-planner --name ICEYE --lat 46.948 --lon 7.447 --time 2026-11-14T10:30Z --window 1h

# Rank the candidates by a known/expected incidence angle (sanity check)
uv run eo-pass-planner --name ICEYE --lat 46.948 --lon 7.447 --time 2026-11-14T10:30Z --incidence 28

# Keep only candidates within +/-3 deg of that incidence
uv run eo-pass-planner --name ICEYE --lat 46.948 --lon 7.447 --time 2026-11-14T10:30Z --incidence 28 --inc-tol 3

# Optical: rank by an expected off-nadir angle, keep passes within +/-3 deg of it
uv run eo-pass-planner --family Pleiades --lat 46.948 --lon 7.447 --time 2026-11-14T10:30Z --window 3h --off-nadir 12 --onad-tol 3

# Optical over the next 2 days, off-nadir 5-20 deg, sun at least 15 deg up
uv run eo-pass-planner --type optical --lat 46.948 --lon 7.447 --start now --end +2d --off-nadir-min 5 --off-nadir-max 20 --min-sun 15

# Only optical satellites that deliver 1 m GSD or better at this geometry
uv run eo-pass-planner --type optical --lat 46.948 --lon 7.447 --start now --end +7d --max-gsd 1

# Optical satellites with SWIR, at 5 m or better in SWIR
uv run eo-pass-planner --type optical --band swir --lat 46.948 --lon 7.447 --start now --end +7d --max-gsd 5

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
| `gsd` | optical: expected GSD (m) at this pass geometry, finest band or the `--band` selection, see [Resolution](#resolution-gsd-and-bands) (`-` if unknown) |
| `grd_km` | ground distance sub-satellite point → target |
| `sun` | sun elevation at the target (deg) |
| `LST` | local mean solar time at the target |
| `TLEage` | time between the orbital-element epoch and the predicted pass |
| `dInc` | predicted minus expected incidence (only with `--incidence`) |
| `dOnad` | predicted minus expected off-nadir angle (only with `--off-nadir`) |

All times are UTC. The CSV writes them without a UTC offset, because Excel
would otherwise convert them to local time; JSON keeps the offset.

The CSV/JSON output also contains slant range, altitude, sub-satellite point,
inclination and the element epoch.

## Resolution (GSD) and bands

For optical satellites the registry holds the **nadir GSD per band group**:
`pan`, `vis` (blue, green, red), `nir`, `rededge`, `swir`, `tir`. These are
native sensor values from public specifications, not resampled product pixel
sizes. The tool scales them to the pass geometry. The pixel footprint grows
with slant range `R` relative to the altitude `h`, and across track also with
the incidence angle:

    along = gsd0 * R / h        cross = along / cos(incidence)        gsd = sqrt(along * cross)

`gsd` is the geometric mean, as vendors usually quote off-nadir GSD. For example,
WorldView-3 at 0.31 m at nadir gives 0.34 m at 20° off-nadir, matching Maxar's figure.

* Without `--band`, `gsd` is the finest band, usually pan.
* `--band swir` keeps only satellites with SWIR, and `gsd` then refers to SWIR.
  With several groups, e.g. `--band vis nir`, all are required and `gsd` is the
  coarsest of them.
* `--max-gsd 1` keeps only passes with `gsd <= 1 m`. Satellites that cannot
  reach it even at nadir are skipped; `--all` shows them with the reason.
* CSV/JSON also contain `gsd_nadir`, `gsd_along`, `gsd_cross` and `band_gsd`,
  the GSD of every band group at the pass geometry. `--list` shows the nadir
  values.

Check the values for the missions you rely on: they are approximate and some
are missing. SAR resolution depends on the imaging mode rather than mainly on
the angle, so SAR satellites have no GSD here.

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

[`src/eo_pass_planner/registry.yaml`](src/eo_pass_planner/registry.yaml) defines
satellite families: how to recognise their satellites, sensor type, operator,
country, look side, viewing envelope and bands.

To add or change satellites, create your own registry file. It is merged over
the bundled one, so it only needs what you add or change:

* Linux: `~/.config/eo-pass-planner/registry.yaml`
* Windows: `C:\Users\<you>\.config\eo-pass-planner\registry.yaml`

```yaml
families:
  Gaofen-2:                   # new family
    pattern: '^GAOFEN-2'
    type: optical
    country: China
    off_nadir: [0, 35]
    min_sun_elevation: 10
    bands: {pan: 0.8, vis: 3.2, nir: 3.2}
  WorldView:                  # change one field of a bundled family
    off_nadir: [0, 30]
  Landsat: null               # remove a bundled family
```

Satellites are matched by `norad` numbers or `cospar` ids (a prefix such as
`2024-149` matches a whole launch) if given, otherwise by the name `pattern`.
In patterns, dashes, underscores and spaces are interchangeable and optional,
so `SENTINEL-2` also matches `SENTINEL 2A` and `SENTINEL2A`. Families from your
file are checked before the bundled ones, so they can take single satellites
out of a broader family.

`--show-registry` prints the merged result and where each family comes from;
copy an entry from there as a starting point. `--registry FILE` merges one more
file on top for a single run. Angle limits can also be overridden per run
(`--inc-min/--inc-max`, `--off-nadir-min/--off-nadir-max`, `--look-side`,
`--min-sun`).

Satellites not in the registry can still be selected by `--name`/`--norad`.
They are treated as type `unknown` with a generous 0–60° off-nadir envelope.

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

## License

GPL-3.0-or-later, see [LICENSE](LICENSE). The bundled copy of
[sgp4](https://pypi.org/project/sgp4/) in `src/eo_pass_planner/_vendor/` is
MIT-licensed, see its own licence file.
