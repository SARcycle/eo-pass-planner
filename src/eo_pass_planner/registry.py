"""Satellite family registry: maps catalogue entries to sensor type and geometry limits.

The bundled registry is overlaid by the user's own file (see ``user_registry_path``)
and by any file given explicitly. Later layers add families or change fields of
existing ones; a family set to ``null`` is removed.
"""

from __future__ import annotations

import os
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

import yaml

# Fallback viewing envelopes when a family does not specify one.
DEFAULT_INCIDENCE = {"sar": (10.0, 60.0)}
DEFAULT_OFF_NADIR = {"optical": (0.0, 30.0), "unknown": (0.0, 60.0)}

BAND_GROUPS = ("pan", "vis", "nir", "rededge", "swir", "tir")
BEST = "best"       # band key for a plain ``gsd:`` value whose band is not known

_SEPARATORS = "-_ "


def normalize_name(name: str) -> str:
    """Satellite name with dashes and underscores turned into single spaces."""
    return " ".join(re.sub(r"[-_]", " ", name).split())


def flexible_pattern(pattern: str) -> re.Pattern:
    """Compile a name regex in which dashes, underscores and spaces are optional
    and interchangeable. Match it against ``normalize_name(name)``."""
    out, i, in_class = [], 0, False
    while i < len(pattern):
        c = pattern[i]
        if c == "\\" and i + 1 < len(pattern):
            nxt = pattern[i + 1]
            out.append(" ?" if nxt in _SEPARATORS and not in_class else c + nxt)
            i += 2
            continue
        if in_class:
            in_class = c != "]"
        elif c == "[":
            in_class = True
        elif c in _SEPARATORS:
            c = " ?"
        out.append(c)
        i += 1
    return re.compile("".join(out), re.IGNORECASE)


@dataclass(frozen=True, eq=False)
class Family:
    name: str
    pattern: re.Pattern | None = None
    type: str = "unknown"
    operator: str = ""
    country: str = ""
    look_side: str = "both"
    incidence: tuple[float, float] | None = None
    off_nadir: tuple[float, float] | None = None
    min_sun_elevation: float | None = None
    norad: frozenset[int] = frozenset()
    cospar: tuple[str, ...] = ()
    bands: tuple[tuple[re.Pattern, dict[str, float]], ...] = ()   # (name pattern, {band: nadir GSD})
    source: str = "bundled"
    raw: dict = field(default_factory=dict)
    extra: dict = field(default_factory=dict)

    def nadir_bands(self, sat_name: str) -> dict[str, float]:
        """Band groups of this satellite with their nadir GSD (m)."""
        norm = normalize_name(sat_name)
        for rx, bands in self.bands:
            if rx.search(norm):
                return bands
        return {}

    def nadir_gsd(self, sat_name: str, bands: Sequence[str] | None = None) -> float | None:
        """Nadir GSD (m): the finest band, or the coarsest of ``bands``.

        None if unknown or if the satellite lacks one of ``bands``.
        """
        have = self.nadir_bands(sat_name)
        if bands:
            if not all(b in have for b in bands):
                return None
            return max(have[b] for b in bands)
        return min(have.values()) if have else None


UNKNOWN = Family(name="(unregistered)")


def user_registry_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "eo-pass-planner" / "registry.yaml"


def _parse_bands(f: dict, family: str) -> tuple[tuple[re.Pattern, dict[str, float]], ...]:
    def check(bands: dict) -> dict[str, float]:
        bad = [b for b in bands if b not in BAND_GROUPS]
        if bad:
            raise ValueError(f"registry family {family!r}: unknown band group(s) {bad}; "
                             f"use {', '.join(BAND_GROUPS)}")
        return {b: float(v) for b, v in bands.items()}

    bands, gsd = f.get("bands"), f.get("gsd")
    if bands:
        if all(isinstance(v, dict) for v in bands.values()):        # per satellite
            return tuple((flexible_pattern(k), check(v)) for k, v in bands.items())
        return ((re.compile(""), check(bands)),)
    if isinstance(gsd, dict):
        return tuple((flexible_pattern(k), {BEST: float(v)}) for k, v in gsd.items())
    if gsd is not None:
        return ((re.compile(""), {BEST: float(gsd)}),)
    return ()


_FIELDS = {"pattern", "type", "operator", "country", "look_side", "incidence", "off_nadir",
           "min_sun_elevation", "norad", "cospar", "bands", "gsd"}


def _family(name: str, f: dict, source: str) -> Family:
    if not (f.get("pattern") or f.get("norad") or f.get("cospar")):
        raise ValueError(f"registry family {name!r} needs a pattern, norad or cospar")
    return Family(
        name=name,
        pattern=flexible_pattern(f["pattern"]) if f.get("pattern") else None,
        type=str(f.get("type", "unknown")).lower(),
        operator=str(f.get("operator", "")),
        country=str(f.get("country", "")),
        look_side=str(f.get("look_side", "both")).lower(),
        incidence=tuple(f["incidence"]) if f.get("incidence") else None,
        off_nadir=tuple(f["off_nadir"]) if f.get("off_nadir") else None,
        min_sun_elevation=f.get("min_sun_elevation"),
        norad=frozenset(int(n) for n in f.get("norad") or ()),
        cospar=tuple(str(c).upper() for c in f.get("cospar") or ()),
        bands=_parse_bands(f, name),
        source=source,
        raw=f,
        extra={k: v for k, v in f.items() if k not in _FIELDS},
    )


def registry_layers(path: str | Path | None = None, *, user: bool = True) -> list[tuple[str, str]]:
    """(source, YAML text) of every registry layer, bundled first."""
    layers = [("bundled", resources.files("eo_pass_planner").joinpath("registry.yaml")
               .read_text(encoding="utf-8"))]
    upath = user_registry_path()
    if user and upath.exists():
        layers.append((str(upath), upath.read_text(encoding="utf-8")))
    if path is not None:
        layers.append((str(path), Path(path).read_text(encoding="utf-8")))
    return layers


def load_registry(path: str | Path | None = None, *, user: bool = True) -> list[Family]:
    """The bundled registry, overlaid by the user file and by ``path`` if given."""
    merged: dict[str, tuple[dict, str]] = {}
    for source, text in registry_layers(path, user=user):
        added: dict[str, tuple[dict, str]] = {}
        for name, f in ((yaml.safe_load(text) or {}).get("families") or {}).items():
            if f is None:
                merged.pop(name, None)
            elif name in merged:
                base, src = merged[name]
                merged[name] = ({**base, **f}, f"{src} + {source}")
            else:
                added[name] = (dict(f), source)
        # Families added by a later layer are checked first, so they can carve
        # satellites out of a broader bundled family.
        merged = {**added, **merged} if source != "bundled" else {**merged, **added}
    return [_family(name, f, src) for name, (f, src) in merged.items()]


def classify(object_name: str, families: list[Family], *, norad: int | None = None,
             cospar: str | None = None) -> Family:
    """The family a catalogue entry belongs to: by NORAD/COSPAR id first, then by name."""
    cospar = (cospar or "").upper()
    for fam in families:
        if (norad is not None and norad in fam.norad) or \
                (cospar and any(cospar.startswith(c) for c in fam.cospar)):
            return fam
    norm = normalize_name(object_name)
    for fam in families:
        if fam.pattern is not None and fam.pattern.search(norm):
            return fam
    return UNKNOWN
