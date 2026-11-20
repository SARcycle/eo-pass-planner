"""Pass prediction and acquisition geometry over a ground target.

All vector maths happens in the Earth-fixed ITRS frame (km, km/s). The
satellite velocity in that frame already includes Earth rotation, so the
heading derived from it is the heading of the *ground track*, which is what
SAR/optical product metadata report.

Acquisition instant
-------------------
For every pass we solve for the instant where the line of sight from the
satellite to the target is perpendicular to the Earth-fixed velocity:

    f(t) = (T - r(t)) . v(t) = 0

For a SAR this is the zero-Doppler time (the azimuth time of the target in a
zero-Doppler focused product); for any sensor it is also the time of closest
approach, i.e. when the target is broadside / perpendicular to the track.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, asdict
from datetime import datetime, timezone

import numpy as np

from .catalog import Candidate
from .orbit import datetime_from_jd, geodetic_to_itrs, itrs_to_geodetic, jd_from_datetime


@dataclass
class Pass:
    satellite: str
    norad: int
    family: str
    type: str
    time_utc: datetime            # zero-Doppler / closest-approach time
    offset_min: float             # minutes relative to the requested time
    direction: str                # ASC / DESC
    look_side: str                # LEFT / RIGHT (target relative to flight direction)
    heading_track: float          # ground-track heading at the sub-satellite point, deg from N, clockwise
    heading_scene: float          # flight direction expressed in the target's local frame (deg)
    look_azimuth: float           # azimuth of the line of sight at the target (sat -> target), deg
    incidence: float              # ellipsoid incidence angle at the target, deg
    off_nadir: float              # off-nadir (look) angle at the satellite, deg
    gsd_nadir: float | None       # nadir GSD from the registry (m)
    gsd: float | None             # GSD at this geometry (m)
    gsd_along: float | None       # along-track GSD (m)
    gsd_cross: float | None       # across-track GSD (m)
    band_gsd: str                 # GSD per band group at this geometry, e.g. "pan 0.34 vis 1.36"
    ground_range_km: float        # sub-satellite point -> target
    slant_range_km: float
    altitude_km: float
    sub_lat: float
    sub_lon: float
    sun_elevation: float          # at the target, deg
    local_solar_time: str         # mean solar time at the target (HH:MM)
    inclination: float
    tle_epoch: datetime
    tle_age_days: float           # acquisition time minus element epoch
    feasible: bool = True
    reason: str = ""
    incidence_delta: float | None = None
    off_nadir_delta: float | None = None

    def as_dict(self, plain_times: bool = False) -> dict:
        """Fields as a dict. ``plain_times`` drops the UTC offset from the times,
        because spreadsheets would otherwise convert them to local time."""
        d = asdict(self)
        if plain_times:
            d["time_utc"] = self.time_utc.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            d["tle_epoch"] = self.tle_epoch.strftime("%Y-%m-%d %H:%M:%S")
        else:
            d["time_utc"] = self.time_utc.isoformat(timespec="milliseconds")
            d["tle_epoch"] = self.tle_epoch.isoformat(timespec="seconds")
        return d


def _enu(lat_deg: float, lon_deg: float):
    """Unit East, North, Up vectors (geodetic) in ECEF."""
    la, lo = math.radians(lat_deg), math.radians(lon_deg)
    e = np.array([-math.sin(lo), math.cos(lo), 0.0])
    n = np.array([-math.sin(la) * math.cos(lo), -math.sin(la) * math.sin(lo), math.cos(la)])
    u = np.array([math.cos(la) * math.cos(lo), math.cos(la) * math.sin(lo), math.sin(la)])
    return e, n, u


def _azimuth(vec, e, n) -> float:
    return math.degrees(math.atan2(float(vec @ e), float(vec @ n))) % 360.0


def _angle(a, b) -> float:
    c = float(a @ b) / (np.linalg.norm(a) * np.linalg.norm(b))
    return math.degrees(math.acos(max(-1.0, min(1.0, c))))


def sun_elevation(t_utc: datetime, lat: float, lon: float) -> float:
    """Approximate solar elevation (deg, ~0.1 deg accuracy, no refraction)."""
    jd = t_utc.timestamp() / 86400.0 + 2440587.5
    d = jd - 2451545.0
    g = math.radians((357.529 + 0.98560028 * d) % 360)
    q = (280.459 + 0.98564736 * d) % 360
    lam = math.radians(q + 1.915 * math.sin(g) + 0.020 * math.sin(2 * g))
    eps = math.radians(23.439 - 0.00000036 * d)
    ra = math.atan2(math.cos(eps) * math.sin(lam), math.cos(lam))
    dec = math.asin(math.sin(eps) * math.sin(lam))
    gmst = math.radians((280.46061837 + 360.98564736629 * d) % 360)
    ha = gmst + math.radians(lon) - ra
    la = math.radians(lat)
    s = math.sin(la) * math.sin(dec) + math.cos(la) * math.cos(dec) * math.cos(ha)
    return math.degrees(math.asin(max(-1.0, min(1.0, s))))


def off_nadir_gsd(gsd_nadir: float, slant_km: float, altitude_km: float,
                  incidence_deg: float) -> tuple[float, float]:
    """Along- and across-track GSD (m) for a sensor with ``gsd_nadir`` viewed off-nadir.

    The pixel footprint grows with slant range; across track it also grows
    with the incidence angle.
    """
    along = gsd_nadir * slant_km / altitude_km
    return along, along / math.cos(math.radians(incidence_deg))


def _refine(sat, target, jd: float, fr_a: float, fr_b: float, iters: int = 30) -> float:
    """Bisection on f(t) = (T - r).v between two times."""
    for _ in range(iters):
        mid = 0.5 * (fr_a + fr_b)
        r, v = sat.state(jd, mid)
        if (target - r) @ v > 0:
            fr_a = mid
        else:
            fr_b = mid
    return 0.5 * (fr_a + fr_b)


def find_passes(candidates: list[Candidate], lat: float, lon: float, *,
                start: datetime, end: datetime, ref_time: datetime | None = None,
                height_m: float = 0.0, step_s: float = 20.0,
                incidence_range: tuple[float, float] | None = None,
                off_nadir_range: tuple[float, float] | None = None,
                min_sun_elevation: float | None = None,
                look_side: str | None = None,
                max_gsd: float | None = None,
                bands: Sequence[str] | None = None,
                include_infeasible: bool = False) -> list[Pass]:
    """Predict every pass of ``candidates`` over the target between start and end.

    Range/sun/look-side arguments override the per-family registry values.
    ``max_gsd`` rejects passes with a coarser or unknown GSD. With ``bands``,
    GSD refers to the coarsest of those band groups and satellites lacking
    one of them are rejected.
    Passes that violate the limits are dropped unless ``include_infeasible``.
    """
    start = start.astimezone(timezone.utc)
    end = end.astimezone(timezone.utc)
    ref_time = (ref_time or start + (end - start) / 2).astimezone(timezone.utc)

    target = geodetic_to_itrs(lat, lon, height_m / 1000.0)
    e_t, n_t, u_t = _enu(lat, lon)

    jd0, fr0 = jd_from_datetime(start)
    n_steps = int(math.ceil((end - start).total_seconds() / step_s)) + 1
    fr_grid = fr0 + np.arange(n_steps) * step_s / 86400.0

    passes: list[Pass] = []
    for cand in candidates:
        sat, fam = cand.satellite, cand.family
        r, v = sat.state(jd0, fr_grid)                  # (3, N)
        f = np.einsum("ij,ij->j", target[:, None] - r, v)
        idx = np.nonzero((f[:-1] > 0) & (f[1:] <= 0))[0]
        for i in idx:
            # Cheap reject: target below the satellite's horizon (far side of Earth).
            los = r[:, i] - target
            if los @ u_t <= 0:
                continue
            fr = _refine(sat, target, jd0, fr_grid[i], fr_grid[i + 1])
            rs, vs = sat.state(jd0, fr)
            sub_lat, sub_lon, sub_h = itrs_to_geodetic(rs)
            e_s, n_s, u_s = _enu(sub_lat, sub_lon)

            los_sat_to_tgt = target - rs
            slant = float(np.linalg.norm(los_sat_to_tgt))
            elevation = 90.0 - _angle(rs - target, u_t)
            incidence = 90.0 - elevation
            off_nadir = _angle(los_sat_to_tgt, -u_s)
            right_vec = np.cross(vs, rs / np.linalg.norm(rs))
            side = "RIGHT" if los_sat_to_tgt @ right_vec > 0 else "LEFT"

            # Great-circle ground distance sub-point -> target (mean radius).
            p1, p2 = math.radians(sub_lat), math.radians(lat)
            dl = math.radians(lon - sub_lon)
            ground = 6371.0088 * math.acos(max(-1.0, min(1.0,
                     math.sin(p1) * math.sin(p2) + math.cos(p1) * math.cos(p2) * math.cos(dl))))

            have = fam.nadir_bands(cand.name)
            g0 = fam.nadir_gsd(cand.name, bands)
            f_along, f_cross = off_nadir_gsd(1.0, slant, sub_h, incidence)
            factor = math.sqrt(f_along * f_cross)
            gsd = gsd_along = gsd_cross = None
            if g0 is not None:
                gsd, gsd_along, gsd_cross = g0 * factor, g0 * f_along, g0 * f_cross
            band_gsd = " ".join(f"{b} {v * factor:.2f}" for b, v in have.items())

            when = datetime_from_jd(jd0, fr)
            epoch = sat.epoch.replace(microsecond=0)
            sun = sun_elevation(when, lat, lon)
            lst_h = (when.hour + when.minute / 60 + when.second / 3600 + lon / 15.0) % 24

            p = Pass(
                satellite=cand.name, norad=cand.norad, family=fam.name, type=fam.type,
                time_utc=when, offset_min=(when - ref_time).total_seconds() / 60.0,
                direction="ASC" if vs[2] > 0 else "DESC", look_side=side,
                heading_track=_azimuth(vs, e_s, n_s),
                heading_scene=_azimuth(vs, e_t, n_t),
                look_azimuth=_azimuth(los_sat_to_tgt, e_t, n_t),
                incidence=incidence, off_nadir=off_nadir,
                gsd_nadir=g0, gsd=gsd, gsd_along=gsd_along, gsd_cross=gsd_cross,
                band_gsd=band_gsd,
                ground_range_km=ground, slant_range_km=slant,
                altitude_km=sub_h, sub_lat=sub_lat, sub_lon=sub_lon,
                sun_elevation=sun, local_solar_time=f"{int(lst_h):02d}:{int(lst_h % 1 * 60):02d}",
                inclination=math.degrees(sat.model.inclo), tle_epoch=epoch,
                tle_age_days=(when - epoch).total_seconds() / 86400.0,
            )

            # ---- feasibility against sensor envelope --------------------------
            reasons = []
            ls = (look_side or fam.look_side or "both").lower()
            if ls in ("left", "right") and side.lower() != ls:
                reasons.append(f"look side {side.lower()} (sensor: {ls})")
            inc_rng = incidence_range or (fam.incidence if fam.type == "sar" else None)
            if inc_rng is None and fam.type == "sar" and not off_nadir_range:
                inc_rng = (10.0, 60.0)
            onr = off_nadir_range or fam.off_nadir
            if onr is None and fam.type != "sar" and not incidence_range:
                onr = (0.0, 30.0) if fam.type == "optical" else (0.0, 60.0)
            if inc_rng and not (inc_rng[0] <= incidence <= inc_rng[1]):
                reasons.append(f"incidence {incidence:.1f} outside {inc_rng[0]:g}-{inc_rng[1]:g}")
            if onr and not (onr[0] <= off_nadir <= onr[1]):
                reasons.append(f"off-nadir {off_nadir:.1f} outside {onr[0]:g}-{onr[1]:g}")
            sun_min = min_sun_elevation if min_sun_elevation is not None else fam.min_sun_elevation
            if sun_min is not None and sun < sun_min:
                reasons.append(f"sun {sun:.1f} below {sun_min:g}")
            missing = [b for b in bands or () if b not in have]
            if missing:
                reasons.append(f"no {'/'.join(missing)} band")
            elif max_gsd is not None:
                if gsd is None:
                    reasons.append("GSD unknown")
                elif gsd > max_gsd:
                    reasons.append(f"GSD {gsd:.2f} m > {max_gsd:g} m")
            p.feasible = not reasons
            p.reason = "; ".join(reasons)
            if p.feasible or include_infeasible:
                passes.append(p)
    return passes
