"""Use case: assemble every analysis table for export.

listings, pricing snapshots, calendar snapshots, review dates and search runs.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta

from airbnb_tracker.application.dto import Dataset, ListingRecord
from airbnb_tracker.application.ports import (
    CalendarRepository,
    DatasetExporter,
    ProfileRepository,
    ReviewRepository,
    SearchRunRepository,
    SnapshotRepository,
)
from airbnb_tracker.application.use_cases.price_outliers import detect_price_outliers
from airbnb_tracker.domain.entities import Listing
from airbnb_tracker.domain.profile import Review, reviews_since
from airbnb_tracker.domain.value_objects import GeoPoint, ListingId

RECENT_REVIEWS_WINDOW = timedelta(days=90)


@dataclass(frozen=True, slots=True)
class ExportQuery:
    as_of: datetime
    my_listing_id: ListingId | None = None
    my_location: GeoPoint | None = None
    min_nights_window_days: int = 60


@dataclass(frozen=True, slots=True)
class ExportSources:
    snapshots: SnapshotRepository
    profiles: ProfileRepository
    calendars: CalendarRepository
    reviews: ReviewRepository
    runs: SearchRunRepository


class ExportDatasetUseCase:
    def __init__(self, sources: ExportSources) -> None:
        self._sources = sources

    def execute(self, query: ExportQuery, exporter: DatasetExporter) -> str:
        return exporter.export(self.build(query))

    def build(self, query: ExportQuery) -> Dataset:
        src = self._sources
        snapshots = sorted(src.snapshots.all_snapshots(), key=lambda s: s.captured_at)
        outliers = detect_price_outliers(snapshots)
        profiles = src.profiles.latest_profiles()
        latest_calendars = src.calendars.latest_calendars()
        reviews = sorted(
            src.reviews.all_reviews(), key=lambda r: (r.listing_id.value, r.created_at)
        )
        reviews_by_listing: dict[ListingId, list[Review]] = defaultdict(list)
        for review in reviews:
            reviews_by_listing[review.listing_id].append(review)
        checked_reviews = src.reviews.latest_review_fetches()

        # The listing page is the most precise source; otherwise the latest search card.
        listings: dict[ListingId, Listing] = {}
        for snapshot in snapshots:
            listings.update((o.listing.id, o.listing) for o in snapshot.offers)
        listings.update((listing_id, p.listing) for listing_id, p in profiles.items())

        records = []
        for listing_id, listing in listings.items():
            calendar = latest_calendars.get(listing_id)
            own_reviews = reviews_by_listing.get(listing_id, [])
            reviews_known = listing_id in checked_reviews
            records.append(
                ListingRecord(
                    listing=listing,
                    is_mine=listing_id == query.my_listing_id,
                    distance_km=_distance(query.my_location, listing),
                    profile=profiles.get(listing_id),
                    minimum_nights=(
                        calendar.typical_min_nights(query.min_nights_window_days)
                        if calendar
                        else None
                    ),
                    last_review_at=max((r.created_at for r in own_reviews), default=None),
                    price_outlier=outliers.get(listing_id),
                    reviews_last_90_days=(
                        reviews_since(own_reviews, query.as_of - RECENT_REVIEWS_WINDOW)
                        if reviews_known
                        else None
                    ),
                )
            )
        records.sort(key=lambda r: (not r.is_mine, r.distance_km or 0.0, r.listing.name))
        return Dataset(
            listings=tuple(records),
            snapshots=tuple(snapshots),
            calendars=tuple(src.calendars.all_calendars()),
            reviews=tuple(reviews),
            search_runs=tuple(src.runs.all_runs()),
        )


def _distance(origin: GeoPoint | None, listing: Listing) -> float | None:
    if origin is None or listing.location is None:
        return None
    return round(origin.distance_km_to(listing.location), 2)
