"""Shared builders and test doubles."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from airbnb_tracker.application.dto import ListingDetails, SearchCriteria
from airbnb_tracker.application.exceptions import GatewayError
from airbnb_tracker.domain.entities import Listing, ListingOffer, PriceQuote, RoomType, Snapshot
from airbnb_tracker.domain.profile import Amenity, CalendarDay
from airbnb_tracker.domain.value_objects import GeoPoint, ListingId, Money, StayPeriod

CENTER = GeoPoint(4.8133, -75.6961)
STAY = StayPeriod(date(2027, 1, 15), date(2027, 1, 22))
NOW = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)
POOL = Amenity("SYSTEM_POOL", "Pool", "Parking and facilities")
WIFI = Amenity("SYSTEM_WIFI", "Wifi", "Internet and office")


class FixedClock:
    def __init__(self, start: datetime = NOW) -> None:
        self.current = start

    def now(self) -> datetime:
        return self.current

    def advance(self, **kwargs: float) -> None:
        self.current += timedelta(**kwargs)


def make_listing(
    listing_id: str = "1",
    *,
    location: GeoPoint | None = CENTER,
    bedrooms: int | None = 2,
    room_type: RoomType = RoomType.ENTIRE_PLACE,
    rating: float | None = 4.8,
) -> Listing:
    return Listing(
        id=ListingId(listing_id),
        name=f"Listing {listing_id}",
        room_type=room_type,
        property_type="Rental unit",
        location=location,
        bedrooms=bedrooms,
        rating=rating,
        review_count=10 if rating else 0,
        photos=("https://img/1.jpg",),
    )


def make_quote(
    listing_id: str = "1",
    nightly: str | int = 100_000,
    *,
    stay: StayPeriod = STAY,
    original_nightly: str | int | None = None,
    captured_at: datetime = NOW,
) -> PriceQuote:
    def total(per_night: str | int) -> Money:
        return Money(Decimal(str(per_night)) * stay.nights, "COP")

    return PriceQuote(
        listing_id=ListingId(listing_id),
        stay=stay,
        total=total(nightly),
        original_total=total(original_nightly) if original_nightly is not None else None,
        captured_at=captured_at,
    )


def make_offer(
    listing_id: str = "1",
    nightly: str | int | None = 100_000,
    *,
    stay: StayPeriod = STAY,
    original_nightly: str | int | None = None,
    location: GeoPoint | None = CENTER,
    bedrooms: int | None = 2,
) -> ListingOffer:
    listing = make_listing(listing_id, location=location, bedrooms=bedrooms)
    quote = (
        make_quote(listing_id, nightly, stay=stay, original_nightly=original_nightly)
        if nightly is not None
        else None
    )
    return ListingOffer(listing, quote)


def make_snapshot(
    *offers: ListingOffer, stay: StayPeriod = STAY, captured_at: datetime = NOW
) -> Snapshot:
    return Snapshot(captured_at, stay, "COP", 2, tuple(offers))


@dataclass
class FakeSearchGateway:
    """Returns the configured offers re-priced for the requested stay."""

    offers: list[ListingOffer] = field(default_factory=list)
    fail: bool = False
    calls: list[SearchCriteria] = field(default_factory=list)

    def search(self, criteria: SearchCriteria) -> list[ListingOffer]:
        self.calls.append(criteria)
        if self.fail:
            raise GatewayError("blocked")
        return [
            make_offer(str(o.listing.id), 100_000, stay=criteria.stay, location=o.listing.location)
            for o in self.offers
        ]


@dataclass
class FakeDetailsGateway:
    known: set[str] = field(default_factory=set)
    calls: list[str] = field(default_factory=list)

    def get_details(self, listing_id: ListingId) -> ListingDetails:
        self.calls.append(str(listing_id))
        if str(listing_id) not in self.known:
            raise GatewayError(f"{listing_id} not found")
        return ListingDetails(
            listing=make_listing(str(listing_id)),
            amenities=frozenset({POOL, WIFI}),
            description="Nice place",
            person_capacity=4,
            is_superhost=True,
        )


@dataclass
class FakeCalendarGateway:
    unavailable_every: int = 2
    failing: set[str] = field(default_factory=set)
    calls: list[str] = field(default_factory=list)

    def get_calendar(self, listing_id: ListingId) -> list[CalendarDay]:
        self.calls.append(str(listing_id))
        if str(listing_id) in self.failing:
            raise GatewayError("timeout")
        start = NOW.date()
        return [
            CalendarDay(start + timedelta(days=i), i % self.unavailable_every != 0, 3)
            for i in range(90)
        ]


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock()
