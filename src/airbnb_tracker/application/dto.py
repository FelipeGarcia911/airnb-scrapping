"""Data transfer objects that cross the application boundary (Introduce Parameter Object)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from airbnb_tracker.domain.entities import Listing, ListingOffer, RoomType, Snapshot
from airbnb_tracker.domain.market_analysis import PriceDistribution, PricePosition
from airbnb_tracker.domain.outliers import OutlierFlag
from airbnb_tracker.domain.profile import (
    Amenity,
    AvailabilitySummary,
    CalendarSnapshot,
    ListingProfile,
    Review,
)
from airbnb_tracker.domain.value_objects import BoundingBox, GeoPoint, Money, StayPeriod


@dataclass(frozen=True, slots=True)
class SearchFilters:
    """Airbnb search filters, mirrored from the website's filter panel."""

    guests: int = 2
    room_type: RoomType | None = None
    min_bedrooms: int | None = None
    min_beds: int | None = None
    min_bathrooms: int | None = None
    price_min: int | None = None
    price_max: int | None = None


class SearchStatus(StrEnum):
    """Outcome of one search."""

    COMPLETE = "complete"  # every row interpreted, not truncated
    PARTIAL = "partial"  # truncated by Airbnb or some rows not interpreted
    EMPTY = "empty"  # Airbnb legitimately returned nothing
    FAILED = "failed"  # nothing usable: see FailureKind

    @property
    def is_usable(self) -> bool:
        return self is not SearchStatus.FAILED


class FailureKind(StrEnum):
    REQUEST = "request_failed"  # network error, blocked or Airbnb error
    FORMAT = "format_changed"  # rows arrived but none could be interpreted
    PRICES = "prices_unreadable"  # listings arrived but no price could be interpreted


def classify_search(
    counts: RowCounts, *, truncated: bool, error: str | None = None
) -> tuple[SearchStatus, FailureKind | None]:
    """Status of a search from what happened to its rows (pure, so it is testable)."""
    if error is not None:
        return SearchStatus.FAILED, FailureKind.REQUEST
    if counts.received == 0:
        return SearchStatus.EMPTY, None
    if counts.parsed == 0:
        return SearchStatus.FAILED, FailureKind.FORMAT
    if counts.kept and not counts.priced and counts.price_excluded:
        return SearchStatus.FAILED, FailureKind.PRICES
    if truncated or counts.parsed < counts.received:
        return SearchStatus.PARTIAL, None
    return SearchStatus.COMPLETE, None


@dataclass(frozen=True, slots=True)
class SearchConditions:
    """Everything that makes two captures comparable, except the dates.

    Prices are only reused across searches with the same conditions: a different number
    of guests, currency, area or filter yields an independent reference.
    """

    source: str  # "area" or "url"
    guests: int
    currency: str
    latitude: float | None = None
    longitude: float | None = None
    radius_km: float | None = None
    url: str | None = None  # without dates or guests
    filters: tuple[tuple[str, Any], ...] = ()
    kept_listings: int | None = None  # nearest N kept (None: all)
    complete_coverage: bool = True

    @classmethod
    def build(
        cls,
        *,
        guests: int,
        currency: str,
        center: GeoPoint | None,
        radius_km: float | None,
        url: str | None,
        filters: dict[str, Any],
        kept_listings: int | None,
        complete_coverage: bool,
    ) -> SearchConditions:
        canonical = tuple(sorted((k, _plain(v)) for k, v in filters.items() if v is not None))
        if url:
            return cls("url", guests, currency.upper(), url=_url_without_stay(url),
                       filters=canonical, kept_listings=kept_listings,
                       complete_coverage=False)  # fmt: skip
        return cls(
            "area",
            guests,
            currency.upper(),
            latitude=round(center.latitude, 5) if center else None,
            longitude=round(center.longitude, 5) if center else None,
            radius_km=round(float(radius_km), 3) if radius_km is not None else None,
            filters=canonical,
            kept_listings=kept_listings,
            complete_coverage=complete_coverage,
        )

    def to_json(self) -> str:
        data = asdict(self)
        data["filters"] = dict(self.filters)
        return json.dumps(data, sort_keys=True, separators=(",", ":"))

    @property
    def key(self) -> str:
        return hashlib.sha256(self.to_json().encode()).hexdigest()[:16]


def _plain(value: Any) -> Any:
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, list | tuple | set | frozenset):
        return sorted(_plain(v) for v in value)
    return value


def _url_without_stay(url: str) -> str:
    """The search URL without the parts set per search (dates, guests), query sorted."""
    parts = urlsplit(url.strip())
    params = sorted(
        (k, v) for k, v in parse_qsl(parts.query)
        if k not in {"checkin", "checkout", "adults", "check_in", "check_out"}
    )  # fmt: skip
    return urlunsplit(parts._replace(query=urlencode(params), fragment=""))


@dataclass(frozen=True, slots=True)
class SearchCriteria:
    """Either an ``area`` or a ``template_url`` (an Airbnb search URL) must be set."""

    stay: StayPeriod
    currency: str
    filters: SearchFilters
    area: BoundingBox | None = None
    template_url: str | None = None

    def __post_init__(self) -> None:
        if (self.area is None) == (self.template_url is None):
            raise ValueError("SearchCriteria needs exactly one of area or template_url")


@dataclass(frozen=True, slots=True)
class SearchResult:
    """``offers`` are the rows that could be interpreted out of ``received`` raw rows
    (``None``: all of them)."""

    offers: list[ListingOffer]
    truncated: bool = False  # Airbnb hit its page cap: results may be incomplete
    received: int | None = None

    @property
    def rows_received(self) -> int:
        return len(self.offers) if self.received is None else self.received


@dataclass(frozen=True, slots=True)
class RowCounts:
    """Rows of one search, step by step: ``received`` from Airbnb → ``parsed`` as listings
    → ``kept`` as competitors (area, filters, limit) → of those, ``priced`` with a
    comparable price or ``price_excluded`` (price not interpretable)."""

    received: int = 0
    parsed: int = 0
    kept: int = 0
    priced: int = 0
    price_excluded: int = 0


@dataclass(frozen=True, slots=True)
class SearchRun:
    """Audit record of one search: its conditions, outcome and row counts.

    ``search_id`` links it to the snapshot it produced; ``conditions`` is the canonical
    JSON behind ``conditions_key``.
    """

    run_id: str
    started_at: datetime
    finished_at: datetime
    stay: StayPeriod
    guests: int
    currency: str
    filters: SearchFilters
    center: GeoPoint | None
    radius_km: float | None
    url: str | None
    complete_coverage: bool
    counts: RowCounts
    status: SearchStatus
    truncated: bool
    error: str | None = None
    failure: FailureKind | None = None
    search_id: str | None = None
    conditions_key: str | None = None
    conditions: str | None = None
    collector_version: str | None = None


@dataclass(frozen=True, slots=True)
class ListingDetails:
    """Everything a listing page tells us, independent of dates."""

    listing: Listing
    amenities: frozenset[Amenity]
    description: str = ""
    person_capacity: int | None = None
    is_superhost: bool = False


# --- report -----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DurationReport:
    """Market prices for one stay length, pooled over every sampled check-in date."""

    nights: int
    check_ins: int
    observations: int
    listings: int
    distribution: PriceDistribution | None
    median_discount_pct: Decimal | None
    discounted_share_pct: float | None
    my_nightly: Money | None
    position: PricePosition | None
    percentile: float | None


@dataclass(frozen=True, slots=True)
class ReportBundle:
    generated_at: datetime
    data_captured_at: datetime | None
    durations: tuple[DurationReport, ...]
    availability: AvailabilitySummary
    availability_window_days: int
    my_unavailable_pct: float | None
    my_min_nights: int | None
    outliers: tuple[OutlierFlag, ...] = ()
    outliers_excluded: bool = True


# --- export -----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ListingRecord:
    listing: Listing
    is_mine: bool
    distance_km: float | None
    profile: ListingProfile | None
    minimum_nights: int | None
    last_review_at: datetime | None = None
    reviews_last_90_days: int | None = None
    price_outlier: OutlierFlag | None = None


@dataclass(frozen=True, slots=True)
class Dataset:
    """The three analysis tables: listings, pricing snapshots and calendar snapshots."""

    listings: tuple[ListingRecord, ...]
    snapshots: tuple[Snapshot, ...]
    calendars: tuple[CalendarSnapshot, ...]
    reviews: tuple[Review, ...] = ()
    search_runs: tuple[SearchRun, ...] = ()
