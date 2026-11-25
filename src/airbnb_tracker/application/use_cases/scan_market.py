"""Use case: search the listings that match your filters and store what they charge.

Every search, successful or not, is also recorded as a ``SearchRun`` so the coverage
of each query (results found, kept, truncated by Airbnb) can be audited later.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from uuid import uuid4

from airbnb_tracker.application.dto import SearchCriteria, SearchFilters, SearchResult, SearchRun
from airbnb_tracker.application.exceptions import GatewayError
from airbnb_tracker.application.ports import (
    Clock,
    ListingSearchGateway,
    SearchRunRepository,
    SnapshotRepository,
)
from airbnb_tracker.domain.entities import ListingOffer, Snapshot
from airbnb_tracker.domain.specifications import AnyListing, ListingSpecification, WithinRadius
from airbnb_tracker.domain.value_objects import BoundingBox, GeoPoint, ListingId, StayPeriod

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ScanMarketCommand:
    """Search either around ``center`` (within ``radius_km``) or with an Airbnb URL."""

    stays: Sequence[StayPeriod]
    currency: str
    filters: SearchFilters = field(default_factory=SearchFilters)
    center: GeoPoint | None = None
    radius_km: float = 1.0
    template_url: str | None = None
    complete_coverage: bool = True
    max_listings: int | None = None  # keep only the N nearest (or top-ranked in URL mode)
    competitor_filter: ListingSpecification = field(default_factory=AnyListing)

    def __post_init__(self) -> None:
        if self.center is None and self.template_url is None:
            raise ValueError("ScanMarketCommand needs a center or a template_url")


@dataclass(frozen=True, slots=True)
class ScanSummary:
    stay: StayPeriod
    found: int
    kept: int
    truncated: bool = False
    error: str | None = None


class ScanMarketUseCase:
    def __init__(
        self,
        gateway: ListingSearchGateway,
        snapshots: SnapshotRepository,
        runs: SearchRunRepository,
        clock: Clock,
        id_factory: Callable[[], str] = lambda: uuid4().hex,
    ) -> None:
        self._gateway = gateway
        self._snapshots = snapshots
        self._runs = runs
        self._clock = clock
        self._id_factory = id_factory

    def execute(
        self,
        command: ScanMarketCommand,
        on_progress: Callable[[ScanSummary, int, int], None] | None = None,
    ) -> list[ScanSummary]:
        """Run every search; ``on_progress(summary, done, total)`` fires after each one."""
        run_id = self._id_factory()
        area, is_competitor = self._area_and_rule(command)
        summaries = []
        for done, stay in enumerate(command.stays, start=1):
            summary = self._scan(run_id, command, stay, area, is_competitor)
            summaries.append(summary)
            if on_progress:
                on_progress(summary, done, len(command.stays))
        return summaries

    @staticmethod
    def _area_and_rule(
        command: ScanMarketCommand,
    ) -> tuple[BoundingBox | None, ListingSpecification]:
        if command.template_url or command.center is None:
            return None, command.competitor_filter
        # The search box is a square: trim its corners to a real radius.
        within = WithinRadius(command.center, command.radius_km)
        return BoundingBox.around(command.center, command.radius_km), (
            within & command.competitor_filter
        )

    def _scan(
        self,
        run_id: str,
        command: ScanMarketCommand,
        stay: StayPeriod,
        area: BoundingBox | None,
        is_competitor: ListingSpecification,
    ) -> ScanSummary:
        started_at = self._clock.now()
        criteria = SearchCriteria(
            stay=stay,
            currency=command.currency,
            filters=command.filters,
            area=area,
            template_url=command.template_url,
        )
        try:
            result = self._gateway.search(criteria)
        except GatewayError as error:
            logger.warning("Market scan failed for %s: %s", stay, error)
            summary = ScanSummary(stay, found=0, kept=0, error=str(error))
            self._record(run_id, command, started_at, summary)
            return summary

        competitors = _unique(o for o in result.offers if is_competitor.is_satisfied_by(o.listing))
        competitors = _nearest(competitors, command.center, command.max_listings)
        self._snapshots.add(
            Snapshot(
                captured_at=started_at,
                stay=stay,
                currency=command.currency,
                guests=command.filters.guests,
                offers=tuple(competitors),
            )
        )
        summary = _summary(stay, result, len(competitors))
        self._record(run_id, command, started_at, summary)
        return summary

    def _record(
        self,
        run_id: str,
        command: ScanMarketCommand,
        started_at: datetime,
        summary: ScanSummary,
    ) -> None:
        url_mode = command.template_url is not None
        self._runs.add_run(
            SearchRun(
                run_id=run_id,
                started_at=started_at,
                finished_at=self._clock.now(),
                stay=summary.stay,
                guests=command.filters.guests,
                currency=command.currency,
                filters=command.filters,
                center=None if url_mode else command.center,
                radius_km=None if url_mode else command.radius_km,
                url=command.template_url,
                complete_coverage=command.complete_coverage and not url_mode,
                results_found=summary.found,
                results_kept=summary.kept,
                truncated=summary.truncated,
                error=summary.error,
            )
        )


def _summary(stay: StayPeriod, result: SearchResult, kept: int) -> ScanSummary:
    return ScanSummary(stay, found=len(result.offers), kept=kept, truncated=result.truncated)


def _nearest(
    offers: list[ListingOffer], center: GeoPoint | None, limit: int | None
) -> list[ListingOffer]:
    """Keep the ``limit`` nearest offers, preserving Airbnb's ranking order among them."""
    if limit is None or len(offers) <= limit:
        return offers
    if center is None:
        return offers[:limit]

    def distance(offer: ListingOffer) -> float:
        location = offer.listing.location
        return center.distance_km_to(location) if location else float("inf")

    kept = {id(o) for o in sorted(offers, key=distance)[:limit]}
    return [o for o in offers if id(o) in kept]


def _unique(offers: Iterable[ListingOffer]) -> list[ListingOffer]:
    seen: dict[ListingId, ListingOffer] = {}
    for offer in offers:
        seen.setdefault(offer.listing.id, offer)
    return list(seen.values())
