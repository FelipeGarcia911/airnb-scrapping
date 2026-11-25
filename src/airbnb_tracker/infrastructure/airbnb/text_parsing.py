"""Parsing of the human-readable strings Airbnb returns ("for 3 nights", "2 bedrooms"...)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum

_CENTS = Decimal("0.01")
_STAY_NIGHTS = re.compile(r"for\s+(\d+)\s+nights?", re.IGNORECASE)
_BEDROOMS = re.compile(r"(\d+)\s+bedrooms?\b")
_BEDS = re.compile(r"(\d+)\s+(?:[a-z-]+\s+)?beds?\b")
_BATHS = re.compile(r"(\d+(?:\.\d+)?)\s+(?:[a-z-]+\s+)?baths?\b")


class PriceUnit(Enum):
    """What a displayed amount covers, as stated by Airbnb's price qualifier."""

    STAY = "stay"  # "for 3 nights"
    NIGHT = "night"  # "night" / "per night"
    WEEK = "week"  # "weekly" (average weekly price)
    MONTH = "month"  # "monthly" (average monthly price, Airbnb uses 30-night months)

    @property
    def nights(self) -> int | None:
        return {PriceUnit.NIGHT: 1, PriceUnit.WEEK: 7, PriceUnit.MONTH: 30}.get(self)

    def to_stay_total(self, amount: Decimal, stay_nights: int) -> Decimal:
        unit_nights = self.nights
        if unit_nights is None:
            return amount
        return (amount * stay_nights / unit_nights).quantize(_CENTS, ROUND_HALF_UP)


def parse_price_unit(text: str | None) -> PriceUnit:
    lowered = (text or "").lower()
    if _STAY_NIGHTS.search(lowered) or not lowered:
        return PriceUnit.STAY
    if "month" in lowered:
        return PriceUnit.MONTH
    if "week" in lowered:
        return PriceUnit.WEEK
    if "night" in lowered:
        return PriceUnit.NIGHT
    return PriceUnit.STAY


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
