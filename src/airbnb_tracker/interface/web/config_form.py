"""Translation between ``config.yaml`` data and the setup form (no Streamlit here).

Keeping this logic pure makes it testable and keeps the page code to layout only.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from typing import Any

ROOM_TYPES = {"Entire place": "entire_place", "Private room": "private_room", "Any": None}
DURATION_CHOICES = (7, 14, 20, 28, 30, 60, 90)
# Optional numbers: the form shows 0 for "no limit", the config stores null.
OPTIONAL_NUMBERS = ("min_bedrooms", "max_bedrooms", "min_bathrooms", "min_beds", "min_nights")
# Money amounts: whole numbers typed with thousands separators; empty = not set.
MONEY_FIELDS = ("nightly_price", "cleaning_fee", "price_min", "price_max")  # fmt: skip
_LISTING_ID = re.compile(r"(?:/rooms/)?(\d{5,25})")


@dataclass
class FormValues:
    """Everything the setup form edits, with UI-friendly types."""

    listing_id: str = ""
    nightly_price: int | None = None  # base rate you publish, before discounts
    weekly_discount_pct: int = 0
    monthly_discount_pct: int = 0
    cleaning_fee: int | None = None
    min_nights: int | None = None
    use_url: bool = False
    latitude: float = 4.8133
    longitude: float = -75.6961
    radius_km: int = 6
    url: str = ""
    guests: int = 2
    room_type_label: str = "Entire place"
    min_bedrooms: int | None = None
    max_bedrooms: int | None = None
    min_bathrooms: int | None = None
    min_beds: int | None = None
    price_min: int | None = None
    price_max: int | None = None
    durations: tuple[int, ...] = (20, 30, 60)
    property_groups: tuple[str, ...] = ()  # empty = every kind of property
    first_check_in_days: int = 7
    every_days: int = 30
    horizon_days: int = 180
    competitors: int = 50
    keep_all_in_search: bool = False
    complete_coverage: bool = True
    currency: str = "COP"
    min_delay_seconds: int = 3
    details_refresh_days: int = 7
    calendar_refresh_days: int = 2


def parse_listing_id(text: str) -> str | None:
    """Accept a bare id or any airbnb.* /rooms/<id> link (query strings are ignored)."""
    match = _LISTING_ID.search(text.strip())
    return match.group(1) if match else None


def from_raw(raw: dict[str, Any]) -> FormValues:
    mine, search = raw.get("my_listing") or {}, raw.get("search") or {}
    stays, enrich = raw.get("stays") or {}, raw.get("enrich") or {}
    scraping = raw.get("scraping") or {}
    defaults = FormValues()
    room_type = search.get("room_type")
    label = next((k for k, v in ROOM_TYPES.items() if v == room_type), "Any")
    competitors = enrich.get("competitors") or search.get("max_listings") or defaults.competitors
    return FormValues(
        listing_id=str(mine.get("id") or ""),
        nightly_price=_opt_int(mine.get("nightly_price")),
        weekly_discount_pct=round(float(mine.get("weekly_discount_pct") or 0)),
        monthly_discount_pct=round(float(mine.get("monthly_discount_pct") or 0)),
        cleaning_fee=_opt_int(mine.get("cleaning_fee")),
        min_nights=_opt_int(mine.get("min_nights")),
        use_url=bool(search.get("url")),
        latitude=float(search.get("latitude", defaults.latitude)),
        longitude=float(search.get("longitude", defaults.longitude)),
        radius_km=max(1, round(float(search.get("radius_km", defaults.radius_km)))),
        url=str(search.get("url") or ""),
        guests=int(search.get("guests", defaults.guests)),
        room_type_label=label,
        min_bedrooms=_opt_int(search.get("min_bedrooms")),
        max_bedrooms=_opt_int(search.get("max_bedrooms")),
        min_bathrooms=_opt_int(search.get("min_bathrooms")),
        min_beds=_opt_int(search.get("min_beds")),
        price_min=_opt_int(search.get("price_min")),
        price_max=_opt_int(search.get("price_max")),
        durations=tuple(int(d) for d in stays.get("durations") or defaults.durations),
        property_groups=tuple(str(g) for g in search.get("property_groups") or ()),
        first_check_in_days=int(stays.get("first_check_in_days", defaults.first_check_in_days)),
        every_days=int(stays.get("every_days", defaults.every_days)),
        horizon_days=int(stays.get("horizon_days", defaults.horizon_days)),
        competitors=int(competitors),
        keep_all_in_search=search.get("max_listings") is None and "max_listings" in search,
        complete_coverage=bool(search.get("complete_coverage", defaults.complete_coverage)),
        currency=str(scraping.get("currency", defaults.currency)),
        min_delay_seconds=round(float(scraping.get("min_delay_seconds", 3))),
        details_refresh_days=round(float(enrich.get("details_refresh_days", 7))),
        calendar_refresh_days=round(float(enrich.get("calendar_refresh_days", 2))),
    )


def to_raw(values: FormValues, base: dict[str, Any]) -> dict[str, Any]:
    """Apply the form onto the existing document, keeping keys the form does not edit."""
    raw = copy.deepcopy(base)
    mine = raw.setdefault("my_listing", {})
    mine["id"] = parse_listing_id(values.listing_id) if values.listing_id else None
    mine["nightly_price"] = values.nightly_price or None
    mine["weekly_discount_pct"] = values.weekly_discount_pct
    mine["monthly_discount_pct"] = values.monthly_discount_pct
    mine["cleaning_fee"] = values.cleaning_fee
    mine["min_nights"] = values.min_nights

    search = raw.setdefault("search", {})
    search.update(
        {
            "latitude": None if values.use_url else round(values.latitude, 6),
            "longitude": None if values.use_url else round(values.longitude, 6),
            "radius_km": values.radius_km,
            "url": values.url.strip() if values.use_url else None,
            "complete_coverage": values.complete_coverage,
            "max_listings": None if values.keep_all_in_search else values.competitors,
            "guests": values.guests,
            "room_type": ROOM_TYPES[values.room_type_label],
            "min_bedrooms": values.min_bedrooms,
            "max_bedrooms": values.max_bedrooms,
            "min_bathrooms": values.min_bathrooms,
            "min_beds": values.min_beds,
            "price_min": values.price_min,
            "price_max": values.price_max,
            "property_groups": list(values.property_groups) or None,
        }
    )
    raw.setdefault("stays", {}).update(
        {
            "durations": sorted(values.durations),
            "first_check_in_days": values.first_check_in_days,
            "every_days": values.every_days,
            "horizon_days": values.horizon_days,
        }
    )
    raw.setdefault("enrich", {}).update(
        {
            "competitors": values.competitors,
            "max_listings_per_run": values.competitors + 1,  # your listing + competitors
            "details_refresh_days": values.details_refresh_days,
            "calendar_refresh_days": values.calendar_refresh_days,
        }
    )
    raw.setdefault("scraping", {}).update(
        {"currency": values.currency.upper(), "min_delay_seconds": values.min_delay_seconds}
    )
    return raw


def _opt_int(value: Any) -> int | None:
    return None if value is None else round(float(value))
