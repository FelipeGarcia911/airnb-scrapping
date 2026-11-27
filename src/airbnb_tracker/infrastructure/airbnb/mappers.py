"""Translate pyairbnb's raw dictionaries into domain objects (anti-corruption layer).

Everything that knows about Airbnb's payload shape lives here, so a format change
only touches this module.
"""

from __future__ import annotations

import html
import logging
import re
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from airbnb_tracker.application.dto import ListingDetails
from airbnb_tracker.domain.entities import Listing, ListingOffer, PriceQuote, RoomType
from airbnb_tracker.domain.exceptions import DomainError
from airbnb_tracker.domain.price_reading import DisplayedPrice, Exclusion, read_price
from airbnb_tracker.domain.profile import Amenity, CalendarDay, Review
from airbnb_tracker.domain.value_objects import GeoPoint, ListingId, Money, StayPeriod
from airbnb_tracker.infrastructure.airbnb.text_parsing import parse_layout, parse_qualifier

logger = logging.getLogger(__name__)

RawData = Mapping[str, Any]

_GUEST_FAVORITE_BADGE = "GUEST_FAVORITE"
_LAYOUT_LINE_TYPES = {"BEDINFO", "BATHROOMINFO"}
_MAX_PHOTOS = 20
_LINE_BREAK = re.compile(r"<br\s*/?>", re.IGNORECASE)
_TAG = re.compile(r"<[^>]+>")


def room_type_from_label(label: str | None) -> RoomType:
    """Map labels like "Entire rental unit", "Private room" or "Room" to a RoomType."""
    text = (label or "").strip().lower()
    if text.startswith("entire"):
        return RoomType.ENTIRE_PLACE
    if text.startswith(("private room", "room")):
        return RoomType.PRIVATE_ROOM
    if text.startswith("shared"):
        return RoomType.SHARED_ROOM
    if text.startswith("hotel"):
        return RoomType.HOTEL_ROOM
    return RoomType.UNKNOWN


def property_type_from_title(title: str | None) -> str:
    """ "Apartment in Medellín" -> "Apartment"."""
    return (title or "").split(" in ")[0].strip()


class SearchResultMapper:
    """Maps one entry of ``pyairbnb.search_all`` to a ListingOffer."""

    def __init__(self, currency: str, room_type_hint: RoomType | None = None) -> None:
        self._currency = currency
        self._room_type_hint = room_type_hint

    def to_offer(
        self, raw: RawData, stay: StayPeriod, captured_at: datetime
    ) -> ListingOffer | None:
        try:
            listing = self._to_listing(raw)
            reading = read_price(self._displayed_price(raw), stay)
            if reading.total is None or not reading.is_comparable:
                no_price = reading.exclusion is Exclusion.NO_AMOUNT
                return ListingOffer(listing, rejected=None if no_price else reading)
            quote = PriceQuote(
                listing_id=listing.id,
                stay=stay,
                total=Money(reading.total, self._currency),
                original_total=(
                    Money(reading.original_total, self._currency)
                    if reading.original_total is not None
                    else None
                ),
                captured_at=captured_at,
                reading=reading,
            )
        except (DomainError, KeyError, TypeError, ValueError, AttributeError) as error:
            logger.debug("Skipping unparseable search result %s: %s", _room_id(raw), error)
            return None
        return ListingOffer(listing, quote)

    def _displayed_price(self, raw: RawData) -> DisplayedPrice:
        """The amount as shown: the discounted one when Airbnb shows a discount."""
        unit = (raw.get("price") or {}).get("unit") or {}
        qualifier_text = str(unit.get("qualifier") or "")
        qualifier = parse_qualifier(qualifier_text)
        return DisplayedPrice(
            amount=_to_decimal(unit.get("discount")) or _to_decimal(unit.get("amount")),
            original_amount=_to_decimal(unit.get("amount")),
            currency=self._currency,
            text=qualifier_text,
            unit=qualifier.unit,
            stated_nights=qualifier.stated_nights,
        )

    def _to_listing(self, raw: RawData) -> Listing:
        title = raw.get("title", "")
        rating = raw.get("rating", {})
        review_count = int(rating.get("reviewCount") or 0)
        layout = parse_layout(" · ".join(_layout_lines(raw.get("structuredContent") or {})))
        return Listing(
            id=ListingId(str(raw["room_id"])),
            name=raw.get("name") or title,
            room_type=self._room_type_hint or _room_type_from_search_title(title),
            property_type=property_type_from_title(title),
            location=_geo_point(raw.get("coordinates") or {}),
            bedrooms=layout.bedrooms,
            beds=layout.beds,
            bathrooms=layout.bathrooms,
            rating=float(rating["value"]) if review_count and rating.get("value") else None,
            review_count=review_count,
            is_guest_favorite=_GUEST_FAVORITE_BADGE in (raw.get("badges") or []),
            photos=_photo_urls(raw.get("images") or []),
        )


class DetailsMapper:
    """Maps ``pyairbnb.get_metadata_from_url`` output to ListingDetails."""

    def to_details(self, listing_id: ListingId, raw: RawData) -> ListingDetails:
        return ListingDetails(
            listing=self._to_listing(listing_id, raw),
            amenities=frozenset(_amenities(raw.get("amenities") or [])),
            description=_plain_text(raw.get("description") or ""),
            person_capacity=_optional_int(raw.get("person_capacity")),
            is_superhost=bool(raw.get("is_super_host")),
        )

    @staticmethod
    def _to_listing(listing_id: ListingId, raw: RawData) -> Listing:
        overview = raw.get("sub_description") or {}
        summary = str(overview.get("title") or "")
        rating = raw.get("rating") or {}
        review_count = int(rating.get("review_count") or 0)
        satisfaction = rating.get("guest_satisfaction")
        # The layout is in the title ("… · 2 bedrooms · 2 baths") or in the items list.
        layout = parse_layout(" · ".join([summary, *map(str, overview.get("items") or [])]))
        return Listing(
            id=listing_id,
            name=raw.get("title") or str(listing_id),
            room_type=room_type_from_label(raw.get("room_type")),
            property_type=property_type_from_title(summary),
            location=_geo_point(raw.get("coordinates") or {}),
            bedrooms=layout.bedrooms,
            beds=layout.beds,
            bathrooms=layout.bathrooms,
            rating=float(satisfaction) if review_count and satisfaction else None,
            review_count=review_count,
            is_guest_favorite=bool(raw.get("is_guest_favorite")),
            photos=_photo_urls(raw.get("images") or []),
        )


class CalendarMapper:
    """Maps ``pyairbnb.get_calendar(return_dataclass=False)`` months to CalendarDays."""

    def to_days(self, months: Iterable[RawData]) -> list[CalendarDay]:
        """One day per date. Days without a date are skipped; a date repeated with
        different availability (overlapping months) becomes unknown."""
        days: dict[date, CalendarDay] = {}
        for month in months:
            for raw in month.get("days") or []:
                if not raw.get("calendarDate"):
                    continue
                day = CalendarDay(
                    day=date.fromisoformat(raw["calendarDate"]),
                    available=_optional_bool(raw.get("available")),
                    min_nights=_optional_int(raw.get("minNights")),
                )
                seen = days.setdefault(day.day, day)
                if seen.available != day.available:
                    days[day.day] = replace(seen, available=None)
        return sorted(days.values(), key=lambda d: d.day)


def _amenities(groups: Iterable[RawData]) -> Iterator[Amenity]:
    for group in groups:
        for value in group.get("values") or []:
            if value.get("available", True) and value.get("title"):
                yield Amenity(
                    code=str(value.get("icon") or ""),
                    name=str(value["title"]),
                    category=str(group.get("title") or ""),
                )


class ReviewMapper:
    """Keeps only id, date, rating and language: no reviewer data or review text."""

    def to_reviews(self, listing_id: ListingId, raw: Iterable[RawData]) -> list[Review]:
        reviews = []
        for item in raw:
            created = item.get("createdAt")
            if not item.get("id") or not created:
                continue
            reviews.append(
                Review(
                    listing_id=listing_id,
                    review_id=str(item["id"]),
                    created_at=datetime.fromisoformat(str(created).replace("Z", "+00:00")),
                    rating=_optional_int(item.get("rating")),
                    language=str(item.get("language") or ""),
                )
            )
        return reviews


def _photo_urls(images: Iterable[object]) -> tuple[str, ...]:
    urls = (img.get("url") if isinstance(img, Mapping) else img for img in images)
    return tuple(str(url) for url in urls if url)[:_MAX_PHOTOS]


def _plain_text(html_text: str) -> str:
    text = _LINE_BREAK.sub("\n", html_text)
    return html.unescape(_TAG.sub("", text)).strip()


def _optional_int(value: object) -> int | None:
    return int(str(value)) if value not in (None, "") else None


def _room_type_from_search_title(title: str) -> RoomType:
    # Search cards say "Room in X" for private rooms and name the property type
    # ("Apartment in X", "Loft in X") for entire places.
    room_type = room_type_from_label(property_type_from_title(title))
    return RoomType.ENTIRE_PLACE if room_type is RoomType.UNKNOWN else room_type


def _layout_lines(structured_content: RawData) -> list[str]:
    lines = structured_content.get("primaryLine") or []
    return [
        str(line.get("body", ""))
        for line in lines
        if isinstance(line, Mapping) and line.get("type") in _LAYOUT_LINE_TYPES
    ]


def _geo_point(raw: RawData) -> GeoPoint | None:
    latitude = raw.get("latitude")
    # pyairbnb's search output misspells the key as "longitud".
    longitude = raw.get("longitude", raw.get("longitud"))
    if not latitude or not longitude:
        return None
    return GeoPoint(float(latitude), float(longitude))


def _to_decimal(value: object) -> Decimal | None:
    if value in (None, "", 0):
        return None
    return Decimal(str(value))


def _optional_bool(value: object) -> bool | None:
    """Only an explicit true/false counts: a missing field stays unknown."""
    return value if isinstance(value, bool) else None


def _room_id(raw: object) -> object:
    return raw.get("room_id") if isinstance(raw, Mapping) else None
