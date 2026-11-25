"""Domain entities and aggregates."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from airbnb_tracker.domain.exceptions import InvalidValueError
from airbnb_tracker.domain.value_objects import GeoPoint, ListingId, Money, StayPeriod


class RoomType(StrEnum):
    ENTIRE_PLACE = "entire_place"
    PRIVATE_ROOM = "private_room"
    SHARED_ROOM = "shared_room"
    HOTEL_ROOM = "hotel_room"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Listing:
    """Stable, date-independent attributes of an Airbnb listing."""

    id: ListingId
    name: str
    room_type: RoomType = RoomType.UNKNOWN
    property_type: str = ""
    location: GeoPoint | None = None
    bedrooms: int | None = None
    beds: int | None = None
    bathrooms: float | None = None
    rating: float | None = None
    review_count: int = 0
    is_guest_favorite: bool = False
    photos: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.review_count < 0:
            raise InvalidValueError("review_count cannot be negative")
        if self.rating is not None and not 0 <= self.rating <= 5:
            raise InvalidValueError(f"rating must be between 0 and 5: {self.rating}")

    @property
    def url(self) -> str:
        return f"https://www.airbnb.com/rooms/{self.id}"


@dataclass(frozen=True, slots=True)
class PriceQuote:
    """Price a guest would pay for ``stay`` at the moment it was captured."""

    listing_id: ListingId
    stay: StayPeriod
    total: Money
    captured_at: datetime
    original_total: Money | None = None

    def __post_init__(self) -> None:
        if self.original_total and self.original_total.currency != self.total.currency:
            raise InvalidValueError("original_total and total must share currency")

    @property
    def nightly(self) -> Money:
        return self.total.divided_by(self.stay.nights)

    @property
    def discount_pct(self) -> Decimal | None:
        if self.original_total is None:
            return None
        return self.total.discount_from(self.original_total)


@dataclass(frozen=True, slots=True)
class ListingOffer:
    """A listing together with its quote for a stay (``quote=None`` means unavailable)."""

    listing: Listing
    quote: PriceQuote | None = None

    def __post_init__(self) -> None:
        if self.quote and self.quote.listing_id != self.listing.id:
            raise InvalidValueError("quote belongs to a different listing")

    @property
    def is_available(self) -> bool:
        return self.quote is not None

    @property
    def nightly_price(self) -> Money | None:
        return self.quote.nightly if self.quote else None


@dataclass(frozen=True, slots=True)
class Snapshot:
    """Aggregate root: every offer observed in one search for one stay period.

    ``offers`` keep Airbnb's ranking order, so ``search_position`` is meaningful.
    """

    captured_at: datetime
    stay: StayPeriod
    currency: str
    guests: int
    offers: tuple[ListingOffer, ...]

    def __post_init__(self) -> None:
        if self.guests < 1:
            raise InvalidValueError("guests must be at least 1")
        ids = [offer.listing.id for offer in self.offers]
        if len(ids) != len(set(ids)):
            raise InvalidValueError("A snapshot cannot contain the same listing twice")
        for offer in self.offers:
            if offer.quote is None:
                continue
            if offer.quote.stay != self.stay:
                raise InvalidValueError("Every quote must belong to the snapshot stay")
            if offer.quote.total.currency != self.currency:
                raise InvalidValueError("Every quote must use the snapshot currency")

    def find(self, listing_id: ListingId) -> ListingOffer | None:
        return next((o for o in self.offers if o.listing.id == listing_id), None)

    def search_position(self, listing_id: ListingId) -> int | None:
        """1-based rank of the listing in the search results."""
        for position, offer in enumerate(self.offers, start=1):
            if offer.listing.id == listing_id:
                return position
        return None
