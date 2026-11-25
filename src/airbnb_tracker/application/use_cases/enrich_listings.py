"""Use case: fetch listing pages, calendars and reviews for the listings found by scans.

Each kind of data has its own refresh interval (pages and reviews weekly, calendars every
few days). The work is incremental: fresh data is skipped, your own listing goes first,
then competitors from nearest to farthest, and at most ``max_listings`` are visited per run.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum

from airbnb_tracker.application.exceptions import GatewayError
from airbnb_tracker.application.ports import (
    CalendarGateway,
    CalendarRepository,
    Clock,
    ListingDetailsGateway,
    ProfileRepository,
    ReviewGateway,
    ReviewRepository,
    SnapshotRepository,
)
from airbnb_tracker.domain.profile import CalendarSnapshot, ListingProfile
from airbnb_tracker.domain.value_objects import GeoPoint, ListingId, StayPeriod

logger = logging.getLogger(__name__)


class EnrichPart(StrEnum):
    DETAILS = "details"
    CALENDARS = "calendars"
    REVIEWS = "reviews"


@dataclass(frozen=True, slots=True)
class EnrichCommand:
    stays: Sequence[StayPeriod]
    priority_ids: Sequence[ListingId] = ()
    center: GeoPoint | None = None
    parts: frozenset[EnrichPart] = field(default_factory=lambda: frozenset(EnrichPart))
    competitors: int | None = 50  # only the N nearest competitors (None = all)
    max_listings: int = 50
    details_refresh: timedelta = timedelta(days=7)
    calendar_refresh: timedelta = timedelta(days=2)
    reviews_refresh: timedelta = timedelta(days=7)


@dataclass(frozen=True, slots=True)
class EnrichSummary:
    fetched: dict[EnrichPart, int]
    up_to_date: int
    pending: int
    failed: tuple[ListingId, ...]


@dataclass(frozen=True, slots=True)
class EnrichGateways:
    details: ListingDetailsGateway
    calendars: CalendarGateway
    reviews: ReviewGateway


@dataclass(frozen=True, slots=True)
class EnrichRepositories:
    snapshots: SnapshotRepository
    profiles: ProfileRepository
    calendars: CalendarRepository
    reviews: ReviewRepository


class EnrichListingsUseCase:
    def __init__(
        self, gateways: EnrichGateways, repositories: EnrichRepositories, clock: Clock
    ) -> None:
        self._gateways = gateways
        self._repos = repositories
        self._clock = clock

    def execute(self, command: EnrichCommand) -> EnrichSummary:
        now = self._clock.now()
        candidates = self._candidates(command)
        work = self._plan(candidates, now, command)
        batch = work[: max(command.max_listings, 0)]

        fetched = dict.fromkeys(EnrichPart, 0)
        failed: list[ListingId] = []
        for listing_id, parts in batch:
            ok = True
            for part in parts:
                if self._fetch(part, listing_id, now):
                    fetched[part] += 1
                else:
                    ok = False
            if not ok:
                failed.append(listing_id)

        return EnrichSummary(
            fetched=fetched,
            up_to_date=len(candidates) - len(work),
            pending=len(work) - len(batch),
            failed=tuple(failed),
        )

    def _candidates(self, command: EnrichCommand) -> list[ListingId]:
        """Your listing first, then the ``competitors`` nearest scanned listings."""
        distances: dict[ListingId, float] = {}
        for stay in command.stays:
            snapshot = self._repos.snapshots.latest(stay)
            for offer in snapshot.offers if snapshot else ():
                location = offer.listing.location
                distances[offer.listing.id] = (
                    command.center.distance_km_to(location)
                    if command.center and location
                    else float("inf")
                )
        nearest = sorted(distances, key=lambda listing_id: distances[listing_id])
        nearest = [i for i in nearest if i not in command.priority_ids]
        if command.competitors is not None:
            nearest = nearest[: max(command.competitors, 0)]
        return list(dict.fromkeys([*command.priority_ids, *nearest]))

    def _plan(
        self, candidates: Sequence[ListingId], now: datetime, command: EnrichCommand
    ) -> list[tuple[ListingId, list[EnrichPart]]]:
        last_fetch = {
            EnrichPart.DETAILS: {
                i: p.captured_at for i, p in self._repos.profiles.latest_profiles().items()
            },
            EnrichPart.CALENDARS: {
                i: c.captured_at for i, c in self._repos.calendars.latest_calendars().items()
            },
            EnrichPart.REVIEWS: self._repos.reviews.latest_review_fetches(),
        }
        refresh = {
            EnrichPart.DETAILS: command.details_refresh,
            EnrichPart.CALENDARS: command.calendar_refresh,
            EnrichPart.REVIEWS: command.reviews_refresh,
        }
        work = []
        for listing_id in candidates:
            parts = [
                part
                for part in EnrichPart
                if part in command.parts
                and _is_stale(last_fetch[part].get(listing_id), now, refresh[part])
            ]
            if parts:
                work.append((listing_id, parts))
        return work

    def _fetch(self, part: EnrichPart, listing_id: ListingId, now: datetime) -> bool:
        fetchers: dict[EnrichPart, Callable[[ListingId, datetime], None]] = {
            EnrichPart.DETAILS: self._fetch_details,
            EnrichPart.CALENDARS: self._fetch_calendar,
            EnrichPart.REVIEWS: self._fetch_reviews,
        }
        try:
            fetchers[part](listing_id, now)
        except GatewayError as error:
            logger.warning("Could not load %s of %s: %s", part.value, listing_id, error)
            return False
        return True

    def _fetch_details(self, listing_id: ListingId, now: datetime) -> None:
        details = self._gateways.details.get_details(listing_id)
        self._repos.profiles.save_profile(
            ListingProfile(
                listing=details.listing,
                captured_at=now,
                amenities=details.amenities,
                description=details.description,
                person_capacity=details.person_capacity,
                is_superhost=details.is_superhost,
            )
        )

    def _fetch_calendar(self, listing_id: ListingId, now: datetime) -> None:
        days = self._gateways.calendars.get_calendar(listing_id)
        self._repos.calendars.save_calendar(CalendarSnapshot(listing_id, now, tuple(days)))

    def _fetch_reviews(self, listing_id: ListingId, now: datetime) -> None:
        reviews = self._gateways.reviews.get_reviews(listing_id)
        self._repos.reviews.save_reviews(listing_id, now, reviews)


def _is_stale(fetched_at: datetime | None, now: datetime, refresh: timedelta) -> bool:
    return fetched_at is None or now - fetched_at >= refresh
