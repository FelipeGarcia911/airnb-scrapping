"""Which collector produced a capture: this package and the pyairbnb version it used."""

from __future__ import annotations

from functools import cache
from importlib.metadata import PackageNotFoundError, version

from airbnb_tracker import __version__


@cache
def collector_version() -> str:
    try:
        pyairbnb = version("pyairbnb")
    except PackageNotFoundError:  # pragma: no cover - always installed with the package
        pyairbnb = "unknown"
    return f"airbnb-tracker {__version__} · pyairbnb {pyairbnb}"
