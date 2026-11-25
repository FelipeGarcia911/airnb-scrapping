"""Translate pyairbnb's raw dictionaries into domain objects (anti-corruption layer).

Everything that knows about Airbnb's payload shape lives here, so a format change
only touches this module.
"""

from __future__ import annotations

import html
import logging
import re
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from airbnb_tracker.application.dto import ListingDetails
from airbnb_tracker.domain.entities import Listing, ListingOffer, PriceQuote, RoomType
from airbnb_tracker.domain.exceptions import DomainError
from airbnb_tracker.domain.profile import Amenity, CalendarDay, Review
from airbnb_tracker.domain.value_objects import GeoPoint, ListingId, Money, StayPeriod
from airbnb_tracker.infrastructure.airbnb.text_parsing import parse_layout, parse_price_unit

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


@dataclass(frozen=True, slots=True)
class DisplayedPrice:
    """A price exactly as Airbnb shows it (Introduce Parameter Object)."""

    final_amount: Decimal | None
    original_amount: Decimal | None
    qualifier: str | None

    def to_quote(
        self, listing_id: ListingId, stay: StayPeriod, currency: str, captured_at: datetime
    ) -> PriceQuote | None:
        """Normalise the displayed price into a stay total, whatever unit Airbnb used.

        Airbnb shows "for 7 nights" totals for short stays but *average monthly* prices
        for 28+ nights, so a 60-night "monthly" price must be doubled.
        """
        if not self.final_amount:
            return None
        unit = parse_price_unit(self.qualifier)
        has_discount = self.original_amount and self.original_amount != self.final_amount
        return PriceQuote(
            listing_id=listing_id,
            stay=stay,
            total=Money(unit.to_stay_total(self.final_amount, stay.nights), currency),
            original_total=(
                Money(unit.to_stay_total(self.original_amount, stay.nights), currency)
                if has_discount and self.original_amount
                else None
            ),
            captured_at=captured_at,
        )


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
            unit = raw.get("price", {}).get("unit", {})
            price = DisplayedPrice(
                final_amount=_to_decimal(unit.get("discount")) or _to_decimal(unit.get("amount")),
                original_amount=_to_decimal(unit.get("amount")),
                qualifier=unit.get("qualifier"),
            )
            quote = price.to_quote(listing.id, stay, self._currency, captured_at)
        except (DomainError, KeyError, TypeError, ValueError) as error:
            logger.debug("Skipping unparseable search result %s: %s", raw.get("room_id"), error)
            return None
        return ListingOffer(listing, quote)

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
        summary = (raw.get("sub_description") or {}).get("title", "")
        rating = raw.get("rating") or {}
        review_count = int(rating.get("review_count") or 0)
        satisfaction = rating.get("guest_satisfaction")
        layout = parse_layout(summary)
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
        return [
            CalendarDay(
                day=date.fromisoformat(day["calendarDate"]),
                available=bool(day.get("available")),
                min_nights=_optional_int(day.get("minNights")),
            )
            for month in months
            for day in month.get("days") or []
            if day.get("calendarDate")
        ]


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
