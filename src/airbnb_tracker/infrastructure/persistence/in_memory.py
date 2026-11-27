from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from airbnb_tracker.application.dto import SearchRun
from airbnb_tracker.domain.entities import Snapshot
from airbnb_tracker.domain.profile import CalendarSnapshot, ListingProfile, Review
from airbnb_tracker.domain.value_objects import ListingId, StayPeriod


class InMemoryRepository:
    """Same contract as SqliteRepository (Liskov); used by tests."""

    def __init__(self) -> None:
        self._snapshots: list[Snapshot] = []
        self._profiles: list[ListingProfile] = []
        self._calendars: list[CalendarSnapshot] = []
        self._reviews: dict[str, Review] = {}
        self._review_fetches: dict[ListingId, datetime] = {}
        self._runs: list[SearchRun] = []

    def add(self, snapshot: Snapshot) -> None:
        self._snapshots.append(snapshot)

    def latest(self, stay: StayPeriod, conditions_key: str) -> Snapshot | None:
        failed = {r.search_id for r in self._runs if not r.status.is_usable}
        matches = [
            s
            for s in self._snapshots
            if s.stay == stay and s.conditions_key == conditions_key and s.search_id not in failed
        ]
        return max(matches, key=lambda s: s.captured_at, default=None)

    def all_snapshots(self) -> list[Snapshot]:
        return list(self._snapshots)

    def save_profile(self, profile: ListingProfile) -> None:
        self._profiles.append(profile)

    def latest_profiles(self) -> dict[ListingId, ListingProfile]:
        return {p.listing_id: p for p in sorted(self._profiles, key=lambda p: p.captured_at)}

    def save_calendar(self, calendar: CalendarSnapshot) -> None:
        self._calendars.append(calendar)

    def latest_calendars(self) -> dict[ListingId, CalendarSnapshot]:
        return {c.listing_id: c for c in sorted(self._calendars, key=lambda c: c.captured_at)}

    def all_calendars(self) -> list[CalendarSnapshot]:
        return list(self._calendars)

    def save_reviews(
        self, listing_id: ListingId, fetched_at: datetime, reviews: Sequence[Review]
    ) -> None:
        for review in reviews:
            self._reviews.setdefault(review.review_id, review)
        self._review_fetches[listing_id] = fetched_at

    def latest_review_fetches(self) -> dict[ListingId, datetime]:
        return dict(self._review_fetches)

    def all_reviews(self) -> list[Review]:
        return sorted(self._reviews.values(), key=lambda r: (r.listing_id.value, r.created_at))

    def add_run(self, run: SearchRun) -> None:
        self._runs.append(run)

    def all_runs(self) -> list[SearchRun]:
        return list(self._runs)
