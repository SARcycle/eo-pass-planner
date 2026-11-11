"""Satellite family registry: maps catalogue names to sensor type and geometry limits."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

import yaml

# Fallback viewing envelopes when a family does not specify one.
DEFAULT_INCIDENCE = {"sar": (10.0, 60.0)}
DEFAULT_OFF_NADIR = {"optical": (0.0, 30.0), "unknown": (0.0, 60.0)}


@dataclass(frozen=True)
class Family:
    name: str
    pattern: re.Pattern
    type: str = "unknown"
    operator: str = ""
    country: str = ""
    look_side: str = "both"
    incidence: tuple[float, float] | None = None
    off_nadir: tuple[float, float] | None = None
    min_sun_elevation: float | None = None
    extra: dict = field(default_factory=dict, compare=False)


UNKNOWN = Family(name="(unregistered)", pattern=re.compile(r"$^"))


def load_registry(path: str | Path | None = None) -> list[Family]:
    """Load the registry YAML (the bundled one unless ``path`` is given)."""
    if path is None:
        text = resources.files("eo_pass_planner").joinpath("registry.yaml").read_text()
    else:
        text = Path(path).read_text()
    data = yaml.safe_load(text) or {}
    families = []
    for name, f in (data.get("families") or {}).items():
        known = {"pattern", "type", "operator", "country", "look_side",
                 "incidence", "off_nadir", "min_sun_elevation"}
        families.append(
            Family(
                name=name,
                pattern=re.compile(f["pattern"], re.IGNORECASE),
                type=str(f.get("type", "unknown")).lower(),
                operator=str(f.get("operator", "")),
                country=str(f.get("country", "")),
                look_side=str(f.get("look_side", "both")).lower(),
                incidence=tuple(f["incidence"]) if f.get("incidence") else None,
                off_nadir=tuple(f["off_nadir"]) if f.get("off_nadir") else None,
                min_sun_elevation=f.get("min_sun_elevation"),
                extra={k: v for k, v in f.items() if k not in known},
            )
        )
    return families


def classify(object_name: str, families: list[Family]) -> Family:
    """Return the first family whose pattern matches the satellite name."""
    for fam in families:
        if fam.pattern.search(object_name):
            return fam
    return UNKNOWN
