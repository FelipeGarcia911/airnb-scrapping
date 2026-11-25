from __future__ import annotations

from airbnb_tracker.domain.entities import Snapshot
from airbnb_tracker.domain.profile import CalendarSnapshot, ListingProfile
from airbnb_tracker.domain.value_objects import ListingId, StayPeriod


class InMemoryRepository:
    """Same contract as SqliteRepository (Liskov); used by tests."""

    def __init__(self) -> None:
        self._snapshots: list[Snapshot] = []
        self._profiles: list[ListingProfile] = []
        self._calendars: list[CalendarSnapshot] = []

    def add(self, snapshot: Snapshot) -> None:
        self._snapshots.append(snapshot)

    def latest(self, stay: StayPeriod) -> Snapshot | None:
        matches = [s for s in self._snapshots if s.stay == stay]
        return max(matches, key=lambda s: s.captured_at, default=None)

    def all_snapshots(self) -> list[Snapshot]:
        return list(self._snapshots)

    def save_profile(self, profile: ListingProfile) -> None:
        self._profiles.append(profile)

    def latest_profiles(self) -> dict[ListingId, ListingProfile]:
        latest: dict[ListingId, ListingProfile] = {}
        for profile in sorted(self._profiles, key=lambda p: p.captured_at):
            latest[profile.listing_id] = profile
        return latest

    def save_calendar(self, calendar: CalendarSnapshot) -> None:
        self._calendars.append(calendar)

    def latest_calendars(self) -> dict[ListingId, CalendarSnapshot]:
        latest: dict[ListingId, CalendarSnapshot] = {}
        for calendar in sorted(self._calendars, key=lambda c: c.captured_at):
            latest[calendar.listing_id] = calendar
        return latest

    def all_calendars(self) -> list[CalendarSnapshot]:
        return list(self._calendars)
