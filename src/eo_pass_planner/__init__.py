"""eo-pass-planner: predict satellite passes and acquisition geometry over a target."""

from .catalog import (Candidate, cache_age_hours, fetch_gp, records_from_tle_file,
                      refresh_selected, select)
from .geometry import Pass, find_passes, sun_elevation
from .registry import Family, load_registry


def main() -> None:
    import sys

    from .cli import main as _main

    sys.exit(_main())


__all__ = ["Candidate", "Family", "Pass", "cache_age_hours", "fetch_gp", "find_passes",
           "load_registry", "main", "records_from_tle_file", "refresh_selected", "select",
           "sun_elevation"]
