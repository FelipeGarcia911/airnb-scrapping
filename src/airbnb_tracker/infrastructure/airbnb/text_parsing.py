"""Parsing of the human-readable strings Airbnb returns ("for 3 nights", "2 bedrooms"...)."""

from __future__ import annotations

import re
from dataclasses import dataclass

from airbnb_tracker.domain.price_reading import PriceUnit

_STAY_NIGHTS = re.compile(r"\bfor\s+(\d+)\s+nights?\b")
_UNIT_WORDS = (
    (re.compile(r"\bmonth(?:ly)?\b"), PriceUnit.MONTH),
    (re.compile(r"\bweek(?:ly)?\b"), PriceUnit.WEEK),
    (re.compile(r"\b(?:per\s+)?night(?:ly)?\b"), PriceUnit.NIGHT),
)
_BEDROOMS = re.compile(r"(\d+)\s+bedrooms?\b")
_BEDS = re.compile(r"(\d+)\s+(?:[a-z-]+\s+)?beds?\b")
_BATHS = re.compile(r"(\d+(?:\.\d+)?)\s+(?:[a-z-]+\s+)?baths?\b")


@dataclass(frozen=True, slots=True)
class Qualifier:
    """The unit a price qualifier states, and the nights of "for N nights"."""

    unit: PriceUnit
    stated_nights: int | None = None


def parse_qualifier(text: str | None) -> Qualifier:
    """Read Airbnb's price qualifier (searches run in English).

    Only explicit wording is trusted: a missing or unrecognised qualifier is
    ``UNKNOWN``, never assumed to be a stay total.
    """
    lowered = (text or "").strip().lower()
    stay = _STAY_NIGHTS.search(lowered)
    if stay:
        return Qualifier(PriceUnit.STAY, int(stay.group(1)))
    for pattern, unit in _UNIT_WORDS:
        if pattern.search(lowered):
            return Qualifier(unit)
    return Qualifier(PriceUnit.UNKNOWN)


@dataclass(frozen=True, slots=True)
class Layout:
    bedrooms: int | None = None
    beds: int | None = None
    bathrooms: float | None = None


def parse_layout(text: str) -> Layout:
    """Parse fragments such as "1 bedroom · 2 beds · 1.5 baths" or "Studio"."""
    lowered = text.lower()
    bedrooms = _first_int(_BEDROOMS, lowered)
    if bedrooms is None and "studio" in lowered:
        bedrooms = 0
    baths = _BATHS.search(lowered)
    return Layout(
        bedrooms=bedrooms,
        beds=_first_int(_BEDS, lowered),
        bathrooms=float(baths.group(1)) if baths else None,
    )


def _first_int(pattern: re.Pattern[str], text: str) -> int | None:
    match = pattern.search(text)
    return int(match.group(1)) if match else None
