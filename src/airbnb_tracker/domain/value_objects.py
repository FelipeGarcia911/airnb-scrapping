"""Immutable value objects (Refactoring: *Replace Primitive with Object*).

Prices, dates and coordinates are never passed around as loose floats or strings:
each concept carries its own validation and behaviour.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from functools import total_ordering

from airbnb_tracker.domain.exceptions import CurrencyMismatchError, InvalidValueError

_CENTS = Decimal("0.01")
_EARTH_RADIUS_KM = 6371.0088
_KM_PER_DEGREE_LATITUDE = 111.32
_LISTING_ID_PATTERN = re.compile(r"^\d{1,25}$")


@dataclass(frozen=True, slots=True)
class ListingId:
    """Numeric Airbnb room id, kept as text to avoid precision loss."""

    value: str

    def __post_init__(self) -> None:
        normalized = str(self.value).strip()
        if not _LISTING_ID_PATTERN.match(normalized):
            raise InvalidValueError(f"Invalid Airbnb listing id: {self.value!r}")
        object.__setattr__(self, "value", normalized)

    def __str__(self) -> str:
        return self.value


@total_ordering
@dataclass(frozen=True, slots=True)
class Money:
    """A non-negative amount in an ISO-4217 currency."""

    amount: Decimal
    currency: str

    def __post_init__(self) -> None:
        if not isinstance(self.amount, Decimal):
            object.__setattr__(self, "amount", Decimal(str(self.amount)))
        if not self.amount.is_finite() or self.amount < 0:
            raise InvalidValueError(f"Money amount must be a non-negative number: {self.amount}")
        if len(self.currency) != 3 or not self.currency.isalpha():
            raise InvalidValueError(f"Invalid ISO currency code: {self.currency!r}")
        object.__setattr__(self, "currency", self.currency.upper())

    @classmethod
    def of(cls, amount: Decimal | int | float | str, currency: str) -> Money:
        return cls(Decimal(str(amount)), currency)

    def divided_by(self, divisor: int) -> Money:
        if divisor <= 0:
            raise InvalidValueError(f"Cannot divide money by {divisor}")
        return Money((self.amount / divisor).quantize(_CENTS, ROUND_HALF_UP), self.currency)

    def times(self, factor: int) -> Money:
        return Money(self.amount * factor, self.currency)

    def discount_from(self, original: Money) -> Decimal | None:
        """Percentage this amount is below ``original`` (``None`` when it is not below it)."""
        self._ensure_same_currency(original)
        if original.amount <= 0 or self.amount >= original.amount:
            return None
        ratio = (original.amount - self.amount) / original.amount * 100
        return ratio.quantize(Decimal("0.1"), ROUND_HALF_UP)

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        self._ensure_same_currency(other)
        return self.amount < other.amount

    def __str__(self) -> str:
        return f"{self.amount.quantize(_CENTS, ROUND_HALF_UP):,} {self.currency}"

    def _ensure_same_currency(self, other: Money) -> None:
        if self.currency != other.currency:
            raise CurrencyMismatchError(f"{self.currency} vs {other.currency}")


@dataclass(frozen=True, slots=True)
class StayPeriod:
    """Check-in / check-out dates of a hypothetical booking used to sample prices."""

    check_in: date
    check_out: date

    def __post_init__(self) -> None:
        if self.check_out <= self.check_in:
            raise InvalidValueError(
                f"check_out ({self.check_out}) must be after check_in ({self.check_in})"
            )

    @classmethod
    def starting(cls, check_in: date, nights: int) -> StayPeriod:
        if nights < 1:
            raise InvalidValueError("A stay needs at least one night")
        return cls(check_in, check_in + timedelta(days=nights))

    @property
    def nights(self) -> int:
        return (self.check_out - self.check_in).days

    def __str__(self) -> str:
        return f"{self.check_in.isoformat()} → {self.check_out.isoformat()} ({self.nights}n)"


@dataclass(frozen=True, slots=True)
class GeoPoint:
    latitude: float
    longitude: float

    def __post_init__(self) -> None:
        if not -90 <= self.latitude <= 90:
            raise InvalidValueError(f"Latitude out of range: {self.latitude}")
        if not -180 <= self.longitude <= 180:
            raise InvalidValueError(f"Longitude out of range: {self.longitude}")

    def distance_km_to(self, other: GeoPoint) -> float:
        """Great-circle distance using the haversine formula."""
        lat1, lon1, lat2, lon2 = map(
            math.radians, (self.latitude, self.longitude, other.latitude, other.longitude)
        )
        h = (
            math.sin((lat2 - lat1) / 2) ** 2
            + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
        )
        return 2 * _EARTH_RADIUS_KM * math.asin(math.sqrt(h))


@dataclass(frozen=True, slots=True)
class BoundingBox:
    north_east: GeoPoint
    south_west: GeoPoint

    def __post_init__(self) -> None:
        if self.north_east.latitude < self.south_west.latitude:
            raise InvalidValueError("north_east must be north of south_west")

    @classmethod
    def around(cls, center: GeoPoint, radius_km: float) -> BoundingBox:
        if radius_km <= 0:
            raise InvalidValueError("radius_km must be positive")
        delta_lat = radius_km / _KM_PER_DEGREE_LATITUDE
        cos_lat = max(math.cos(math.radians(center.latitude)), 1e-6)
        delta_lon = radius_km / (_KM_PER_DEGREE_LATITUDE * cos_lat)
        return cls(
            north_east=GeoPoint(
                min(center.latitude + delta_lat, 90.0), min(center.longitude + delta_lon, 180.0)
            ),
            south_west=GeoPoint(
                max(center.latitude - delta_lat, -90.0), max(center.longitude - delta_lon, -180.0)
            ),
        )

    @property
    def span_degrees(self) -> float:
        return max(
            self.north_east.latitude - self.south_west.latitude,
            self.north_east.longitude - self.south_west.longitude,
        )
