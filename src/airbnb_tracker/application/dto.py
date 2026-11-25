"""Data transfer objects that cross the application boundary (Introduce Parameter Object)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from airbnb_tracker.domain.entities import Listing, ListingOffer, RoomType, Snapshot
from airbnb_tracker.domain.market_analysis import PriceDistribution, PricePosition
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
    offers: list[ListingOffer]
    truncated: bool = False  # Airbnb hit its page cap: results may be incomplete


@dataclass(frozen=True, slots=True)
class SearchRun:
    """Audit record of one search, to evaluate how well queries cover the market."""

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
    results_found: int
    results_kept: int
    truncated: bool
    error: str | None = None


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


@dataclass(frozen=True, slots=True)
class Dataset:
    """The three analysis tables: listings, pricing snapshots and calendar snapshots."""

    listings: tuple[ListingRecord, ...]
    snapshots: tuple[Snapshot, ...]
    calendars: tuple[CalendarSnapshot, ...]
    reviews: tuple[Review, ...] = ()
    search_runs: tuple[SearchRun, ...] = ()
