"""Use case: fetch listing pages (weekly) and calendars (every few days).

Two requests per listing, so the work is incremental: anything fresher than its
refresh interval is skipped and at most ``max_listings`` listings are visited per run.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from airbnb_tracker.application.exceptions import GatewayError
from airbnb_tracker.application.ports import (
    CalendarGateway,
    CalendarRepository,
    Clock,
    ListingDetailsGateway,
    ProfileRepository,
    SnapshotRepository,
)
from airbnb_tracker.domain.profile import CalendarSnapshot, ListingProfile
from airbnb_tracker.domain.value_objects import ListingId, StayPeriod

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class EnrichCommand:
    stays: Sequence[StayPeriod]
    priority_ids: Sequence[ListingId] = ()
    max_listings: int = 50
    details_refresh: timedelta = timedelta(days=7)
    calendar_refresh: timedelta = timedelta(days=2)


@dataclass(frozen=True, slots=True)
class EnrichSummary:
    details_fetched: int
    calendars_fetched: int
    up_to_date: int
    pending: int
    failed: tuple[ListingId, ...]


class EnrichListingsUseCase:
    def __init__(
        self,
        details_gateway: ListingDetailsGateway,
        calendar_gateway: CalendarGateway,
        *,
        snapshots: SnapshotRepository,
        profiles: ProfileRepository,
        calendars: CalendarRepository,
        clock: Clock,
    ) -> None:
        self._details = details_gateway
        self._calendar = calendar_gateway
        self._snapshots = snapshots
        self._profiles = profiles
        self._calendars = calendars
        self._clock = clock

    def execute(self, command: EnrichCommand) -> EnrichSummary:
        now = self._clock.now()
        candidates = self._candidates(command)
        work = self._plan(candidates, now, command)
        batch = work[: max(command.max_listings, 0)]

        details_fetched = calendars_fetched = 0
        failed: list[ListingId] = []
        for task in batch:
            details_ok = not task.details or self._fetch_details(task.listing_id, now)
            calendar_ok = not task.calendar or self._fetch_calendar(task.listing_id, now)
            details_fetched += int(task.details and details_ok)
            calendars_fetched += int(task.calendar and calendar_ok)
            if not (details_ok and calendar_ok):
                failed.append(task.listing_id)

        return EnrichSummary(
            details_fetched=details_fetched,
            calendars_fetched=calendars_fetched,
            up_to_date=len(candidates) - len(work),
            pending=len(work) - len(batch),
            failed=tuple(failed),
        )

    def _plan(
        self, candidates: Sequence[ListingId], now: datetime, command: EnrichCommand
    ) -> list[_Task]:
        profiles = self._profiles.latest_profiles()
        calendars = self._calendars.latest_calendars()
        tasks = []
        for listing_id in candidates:
            profile, calendar = profiles.get(listing_id), calendars.get(listing_id)
            profile_at = profile.captured_at if profile else None
            calendar_at = calendar.captured_at if calendar else None
            task = _Task(
                listing_id,
                details=_is_stale(profile_at, now, command.details_refresh),
                calendar=_is_stale(calendar_at, now, command.calendar_refresh),
            )
            if task.details or task.calendar:
                tasks.append(task)
        return tasks

    def _candidates(self, command: EnrichCommand) -> list[ListingId]:
        ids: dict[ListingId, None] = dict.fromkeys(command.priority_ids)
        for stay in command.stays:
            if snapshot := self._snapshots.latest(stay):
                ids.update(dict.fromkeys(o.listing.id for o in snapshot.offers))
        return list(ids)

    def _fetch_details(self, listing_id: ListingId, now: datetime) -> bool:
        try:
            details = self._details.get_details(listing_id)
        except GatewayError as error:
            logger.warning("Could not load details of %s: %s", listing_id, error)
            return False
        self._profiles.save_profile(
            ListingProfile(
                listing=details.listing,
                captured_at=now,
                amenities=details.amenities,
                description=details.description,
                person_capacity=details.person_capacity,
                is_superhost=details.is_superhost,
            )
        )
        return True

    def _fetch_calendar(self, listing_id: ListingId, now: datetime) -> bool:
        try:
            days = self._calendar.get_calendar(listing_id)
        except GatewayError as error:
            logger.warning("Could not load calendar of %s: %s", listing_id, error)
            return False
        self._calendars.save_calendar(CalendarSnapshot(listing_id, now, tuple(days)))
        return True


@dataclass(frozen=True, slots=True)
class _Task:
    listing_id: ListingId
    details: bool
    calendar: bool


def _is_stale(captured_at: datetime | None, now: datetime, refresh: timedelta) -> bool:
    return captured_at is None or now - captured_at >= refresh
