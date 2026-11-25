"""Use case: assemble listings, pricing snapshots and calendar snapshots for export."""

from __future__ import annotations

from dataclasses import dataclass

from airbnb_tracker.application.dto import Dataset, ListingRecord
from airbnb_tracker.application.ports import (
    CalendarRepository,
    DatasetExporter,
    ProfileRepository,
    SnapshotRepository,
)
from airbnb_tracker.domain.entities import Listing
from airbnb_tracker.domain.value_objects import GeoPoint, ListingId


@dataclass(frozen=True, slots=True)
class ExportQuery:
    my_listing_id: ListingId | None = None
    my_location: GeoPoint | None = None
    min_nights_window_days: int = 60


class ExportDatasetUseCase:
    def __init__(
        self,
        snapshots: SnapshotRepository,
        profiles: ProfileRepository,
        calendars: CalendarRepository,
    ) -> None:
        self._snapshots = snapshots
        self._profiles = profiles
        self._calendars = calendars

    def execute(self, query: ExportQuery, exporter: DatasetExporter) -> str:
        return exporter.export(self.build(query))

    def build(self, query: ExportQuery) -> Dataset:
        snapshots = sorted(self._snapshots.all_snapshots(), key=lambda s: s.captured_at)
        profiles = self._profiles.latest_profiles()
        latest_calendars = self._calendars.latest_calendars()

        # The listing page is the most precise source; otherwise the latest search card.
        listings: dict[ListingId, Listing] = {}
        for snapshot in snapshots:
            listings.update((o.listing.id, o.listing) for o in snapshot.offers)
        listings.update((listing_id, p.listing) for listing_id, p in profiles.items())

        records = [
            ListingRecord(
                listing=listing,
                is_mine=listing_id == query.my_listing_id,
                distance_km=_distance(query.my_location, listing),
                profile=profiles.get(listing_id),
                minimum_nights=(
                    calendar.typical_min_nights(query.min_nights_window_days)
                    if (calendar := latest_calendars.get(listing_id))
                    else None
                ),
            )
            for listing_id, listing in listings.items()
        ]
        records.sort(key=lambda r: (not r.is_mine, r.distance_km or 0.0, r.listing.name))
        return Dataset(
            listings=tuple(records),
            snapshots=tuple(snapshots),
            calendars=tuple(self._calendars.all_calendars()),
        )


def _distance(origin: GeoPoint | None, listing: Listing) -> float | None:
    if origin is None or listing.location is None:
        return None
    return round(origin.distance_km_to(listing.location), 2)
