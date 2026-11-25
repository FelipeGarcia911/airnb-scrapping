"""Ports (Dependency Inversion): use cases depend on these abstractions only.

Gateways and repositories are split by responsibility (Interface Segregation), so a
use case that only needs search results never depends on calendars or profiles.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from airbnb_tracker.application.dto import Dataset, ListingDetails, ReportBundle, SearchCriteria
from airbnb_tracker.domain.entities import ListingOffer, Snapshot
from airbnb_tracker.domain.profile import CalendarDay, CalendarSnapshot, ListingProfile
from airbnb_tracker.domain.value_objects import ListingId, StayPeriod

# --- gateways (external data sources) ----------------------------------------------------


class ListingSearchGateway(Protocol):
    def search(self, criteria: SearchCriteria) -> list[ListingOffer]:
        """Listings in Airbnb's ranking order. Raises GatewayError on failure."""
        ...


class ListingDetailsGateway(Protocol):
    def get_details(self, listing_id: ListingId) -> ListingDetails:
        """Listing page data. Raises GatewayError on failure."""
        ...


class CalendarGateway(Protocol):
    def get_calendar(self, listing_id: ListingId) -> list[CalendarDay]:
        """Availability of the coming months. Raises GatewayError on failure."""
        ...


# --- repositories (append-only history) --------------------------------------------------


class SnapshotRepository(Protocol):
    def add(self, snapshot: Snapshot) -> None: ...

    def latest(self, stay: StayPeriod) -> Snapshot | None: ...

    def all_snapshots(self) -> list[Snapshot]: ...


class ProfileRepository(Protocol):
    def save_profile(self, profile: ListingProfile) -> None: ...

    def latest_profiles(self) -> dict[ListingId, ListingProfile]: ...


class CalendarRepository(Protocol):
    def save_calendar(self, calendar: CalendarSnapshot) -> None: ...

    def latest_calendars(self) -> dict[ListingId, CalendarSnapshot]: ...

    def all_calendars(self) -> list[CalendarSnapshot]: ...


# --- output ------------------------------------------------------------------------------


class ReportExporter(Protocol):
    def export(self, bundle: ReportBundle) -> str: ...


class DatasetExporter(Protocol):
    def export(self, dataset: Dataset) -> str:
        """Write the dataset and return where it went."""
        ...


class Clock(Protocol):
    def now(self) -> datetime: ...
