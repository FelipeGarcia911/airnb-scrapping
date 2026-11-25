"""Listing page data (refreshed weekly) and calendar observations (every few days)."""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from airbnb_tracker.domain.entities import Listing
from airbnb_tracker.domain.exceptions import InvalidValueError
from airbnb_tracker.domain.value_objects import ListingId


@dataclass(frozen=True, slots=True, eq=False)
class Amenity:
    """An amenity identified by Airbnb's stable code (e.g. ``SYSTEM_POOL``)."""

    code: str
    name: str
    category: str = ""

    @property
    def key(self) -> str:
        return self.code or self.name.strip().lower()

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Amenity) and self.key == other.key

    def __hash__(self) -> int:
        return hash(self.key)


@dataclass(frozen=True, slots=True)
class ListingProfile:
    """What the listing page says, as of ``captured_at``."""

    listing: Listing
    captured_at: datetime
    amenities: frozenset[Amenity]
    description: str = ""
    person_capacity: int | None = None
    is_superhost: bool = False

    @property
    def listing_id(self) -> ListingId:
        return self.listing.id


@dataclass(frozen=True, slots=True)
class CalendarDay:
    day: date
    available: bool
    min_nights: int | None = None


@dataclass(frozen=True, slots=True)
class CalendarSnapshot:
    """One observation of a listing's calendar. Never overwritten: history matters.

    An unavailable day may be a booking, a host block or a stay restriction, so
    availability is an approximation of occupancy, never a confirmed booking.
    """

    listing_id: ListingId
    captured_at: datetime
    days: tuple[CalendarDay, ...]

    def upcoming(self, window_days: int) -> list[CalendarDay]:
        start = self.captured_at.date()
        end = start + timedelta(days=window_days)
        return [d for d in self.days if start <= d.day < end]

    def unavailable_pct(self, window_days: int) -> float | None:
        days = self.upcoming(window_days)
        if not days:
            return None
        return round(sum(1 for d in days if not d.available) / len(days) * 100, 1)

    def typical_min_nights(self, window_days: int) -> int | None:
        """Most frequent minimum stay over the window (hosts rarely vary it)."""
        values = [d.min_nights for d in self.upcoming(window_days) if d.min_nights]
        if not values:
            return None
        return int(statistics.mode(values))

    def __post_init__(self) -> None:
        if len({d.day for d in self.days}) != len(self.days):
            raise InvalidValueError("A calendar snapshot cannot repeat days")


@dataclass(frozen=True, slots=True)
class AvailabilitySummary:
    """Market-level view of calendars: approximate occupancy and minimum stays."""

    listings_measured: int
    median_unavailable_pct: float | None
    median_min_nights: float | None

    @classmethod
    def from_calendars(
        cls, calendars: Sequence[CalendarSnapshot], window_days: int
    ) -> AvailabilitySummary:
        rates = [r for c in calendars if (r := c.unavailable_pct(window_days)) is not None]
        stays = [m for c in calendars if (m := c.typical_min_nights(window_days)) is not None]
        return cls(
            listings_measured=len(rates),
            median_unavailable_pct=round(statistics.median(rates), 1) if rates else None,
            median_min_nights=statistics.median(stays) if stays else None,
        )


@dataclass(frozen=True, slots=True)
class Review:
    """A review reduced to what measures booking activity.

    Reviewer names, photos and texts are deliberately not kept (personal data).
    """

    listing_id: ListingId
    review_id: str
    created_at: datetime
    rating: int | None = None
    language: str = ""


def reviews_since(reviews: Sequence[Review], since: datetime) -> int:
    """Reviews written after ``since``: a proxy for recent completed stays."""
    return sum(1 for review in reviews if review.created_at >= since)
