"""What a captured price means: the amount as shown, its unit and how it was normalised.

Airbnb shows the same amount as a stay total ("for 3 nights"), a nightly price, or an
average weekly or monthly price. A price is only comparable once its unit is known and
consistent with the stay; otherwise it is kept, with the reason, but never enters a
statistic (Replace Primitive with Object: no more bare "total" numbers of unknown meaning).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum

from airbnb_tracker.domain.value_objects import StayPeriod

_CENTS = Decimal("0.01")


class PriceUnit(StrEnum):
    """What a displayed amount covers, as stated next to it."""

    STAY = "stay"  # "for 3 nights": the whole stay
    NIGHT = "night"  # "night" / "per night"
    WEEK = "week"  # "weekly": average weekly price
    MONTH = "month"  # "monthly": average monthly price (Airbnb months are 30 nights)
    UNKNOWN = "unknown"  # missing or unrecognised qualifier

    @property
    def nights(self) -> int | None:
        """Nights one unit covers (None for a stay total or an unknown unit)."""
        return {PriceUnit.NIGHT: 1, PriceUnit.WEEK: 7, PriceUnit.MONTH: 30}.get(self)


class Exclusion(StrEnum):
    """Why a displayed price is left out of every statistic."""

    NO_AMOUNT = "no_amount"
    UNKNOWN_UNIT = "unknown_unit"
    NIGHTS_MISMATCH = "nights_mismatch"  # "for 5 nights" on a 7-night search
    NOT_RECORDED = "not_recorded"  # captured before units were stored


class Normalisation(StrEnum):
    """How the stay total was obtained from the displayed amount."""

    AS_STAY_TOTAL = "stay_total"
    PER_UNIT = "unit_price_x_nights"  # nightly, weekly or monthly price scaled to the stay
    NONE = "none"  # excluded: not normalised


@dataclass(frozen=True, slots=True)
class DisplayedPrice:
    """A price exactly as Airbnb showed it (Introduce Parameter Object)."""

    amount: Decimal | None
    original_amount: Decimal | None
    currency: str
    text: str  # the qualifier as shown, e.g. "for 3 nights", "monthly", ""
    unit: PriceUnit
    stated_nights: int | None = None  # the N in "for N nights"


@dataclass(frozen=True, slots=True)
class PriceReading:
    """The displayed price plus how it was read: normalised totals, or why it was not."""

    displayed: DisplayedPrice
    normalisation: Normalisation
    total: Decimal | None = None
    original_total: Decimal | None = None
    exclusion: Exclusion | None = None

    @property
    def is_comparable(self) -> bool:
        return self.exclusion is None and self.total is not None


def read_price(displayed: DisplayedPrice, stay: StayPeriod) -> PriceReading:
    """Normalise ``displayed`` into a total for ``stay``, or explain why it cannot be."""
    exclusion = _exclusion(displayed, stay)
    if exclusion is not None or displayed.amount is None:
        return PriceReading(displayed, Normalisation.NONE, exclusion=exclusion)
    unit_nights = displayed.unit.nights
    original = displayed.original_amount
    has_original = original is not None and original != displayed.amount
    if unit_nights is None:
        return PriceReading(
            displayed,
            Normalisation.AS_STAY_TOTAL,
            total=displayed.amount,
            original_total=original if has_original else None,
        )
    return PriceReading(
        displayed,
        Normalisation.PER_UNIT,
        total=_scale(displayed.amount, stay.nights, unit_nights),
        original_total=_scale(original, stay.nights, unit_nights) if has_original else None,
    )


def not_recorded(amount: Decimal, currency: str) -> PriceReading:
    """A price stored before units were recorded: its meaning cannot be verified."""
    displayed = DisplayedPrice(amount, None, currency, "", PriceUnit.UNKNOWN)
    return PriceReading(displayed, Normalisation.NONE, exclusion=Exclusion.NOT_RECORDED)


def _exclusion(displayed: DisplayedPrice, stay: StayPeriod) -> Exclusion | None:
    if not displayed.amount:
        return Exclusion.NO_AMOUNT
    if displayed.unit is PriceUnit.UNKNOWN:
        return Exclusion.UNKNOWN_UNIT
    if displayed.unit is PriceUnit.STAY and displayed.stated_nights not in (None, stay.nights):
        return Exclusion.NIGHTS_MISMATCH
    return None


def _scale(amount: Decimal | None, nights: int, unit_nights: int) -> Decimal | None:
    if amount is None:
        return None
    return (amount * nights / unit_nights).quantize(_CENTS, ROUND_HALF_UP)
