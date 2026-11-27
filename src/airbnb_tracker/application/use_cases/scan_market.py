"""Use case: search the listings that match your filters and store what they charge.

Every search, successful or not, is recorded as a ``SearchRun`` with its conditions,
status (complete, partial, empty, failed) and row counts, and linked to the snapshot it
produced. A failed search stores no usable prices, so the last valid capture with the
same conditions keeps serving as the reference.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from uuid import uuid4

from airbnb_tracker.application.dto import (
    FailureKind,
    RowCounts,
    SearchConditions,
    SearchCriteria,
    SearchFilters,
    SearchRun,
    SearchStatus,
    classify_search,
)
from airbnb_tracker.application.exceptions import GatewayError
from airbnb_tracker.application.ports import (
    Clock,
    ListingSearchGateway,
    SearchRunRepository,
    SnapshotRepository,
)
from airbnb_tracker.application.progress import Phase, ProgressEvent, ProgressListener, silent
from airbnb_tracker.domain.entities import ListingOffer, Snapshot
from airbnb_tracker.domain.specifications import AnyListing, ListingSpecification, WithinRadius
from airbnb_tracker.domain.value_objects import BoundingBox, GeoPoint, ListingId, StayPeriod

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ScanMarketCommand:
    """Search either around ``center`` (within ``radius_km``) or with an Airbnb URL."""

    stays: Sequence[StayPeriod]
    currency: str
    conditions: SearchConditions
    filters: SearchFilters = field(default_factory=SearchFilters)
    center: GeoPoint | None = None
    radius_km: float = 1.0
    template_url: str | None = None
    complete_coverage: bool = True
    max_listings: int | None = None  # keep only the N nearest (or top-ranked in URL mode)
    competitor_filter: ListingSpecification = field(default_factory=AnyListing)
    collector_version: str | None = None

    def __post_init__(self) -> None:
        if self.center is None and self.template_url is None:
            raise ValueError("ScanMarketCommand needs a center or a template_url")


@dataclass(frozen=True, slots=True)
class ScanSummary:
    stay: StayPeriod
    counts: RowCounts
    status: SearchStatus
    failure: FailureKind | None = None
    truncated: bool = False
    error: str | None = None
    search_id: str | None = None


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
        progress: ProgressListener = silent,
    ) -> list[ScanSummary]:
        """Run every search, announcing each one before and after it runs."""
        run_id = self._id_factory()
        area, is_competitor = self._area_and_rule(command)
        total = len(command.stays)
        summaries = []
        for index, stay in enumerate(command.stays):
            progress(ProgressEvent(Phase.PRICES, index, total, f"Searching {describe(stay)}"))
            summary = self._scan(run_id, command, stay, area, is_competitor)
            summaries.append(summary)
            progress(ProgressEvent(Phase.PRICES, index + 1, total, _outcome(summary)))
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
        search_id = self._id_factory()
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
            status, failure = classify_search(RowCounts(), truncated=False, error=str(error))
            summary = ScanSummary(stay, RowCounts(), status, failure, error=str(error),
                                  search_id=search_id)  # fmt: skip
            self._record(run_id, command, started_at, summary)
            return summary

        unique = _unique(result.offers)
        competitors = [
            o
            for o in unique
            if is_competitor.is_satisfied_by(o.listing)
            and (o.quote is None or command.filters.allows_nightly(o.quote.nightly.amount))
        ]
        kept = _nearest(competitors, command.center, command.max_listings)
        self._snapshots.add(
            Snapshot(
                captured_at=started_at,
                stay=stay,
                currency=command.currency,
                guests=command.filters.guests,
                offers=tuple(kept),
                search_id=search_id,
                conditions_key=command.conditions.key,
            )
        )
        counts = RowCounts(
            received=result.rows_received,
            parsed=len(result.offers),
            kept=len(kept),
            priced=sum(1 for offer in kept if offer.quote is not None),
            price_excluded=sum(1 for offer in kept if offer.rejected is not None),
            duplicates=len(result.offers) - len(unique),
            filtered_out=len(unique) - len(competitors),
            over_limit=len(competitors) - len(kept),
        )
        status, failure = classify_search(counts, truncated=result.truncated)
        summary = ScanSummary(stay, counts, status, failure, truncated=result.truncated,
                              search_id=search_id)  # fmt: skip
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
                counts=summary.counts,
                status=summary.status,
                truncated=summary.truncated,
                error=summary.error,
                search_id=summary.search_id,
                conditions_key=command.conditions.key,
                conditions=command.conditions.to_json(),
                collector_version=command.collector_version,
                failure=summary.failure,
            )
        )


def describe(stay: StayPeriod) -> str:
    """'30 nights · check-in Jan 1, 2027'"""
    check_in = stay.check_in
    return f"{stay.nights} nights · check-in {check_in:%b} {check_in.day}, {check_in.year}"


def _outcome(summary: ScanSummary) -> str:
    where = describe(summary.stay)
    if summary.status is SearchStatus.FAILED:
        reason = summary.error or (summary.failure.value if summary.failure else "")
        return f"{where}: failed ({reason}), the previous capture is kept"
    if summary.status is SearchStatus.EMPTY:
        return f"{where}: no listings returned"
    counts = summary.counts
    note = " (partial)" if summary.status is SearchStatus.PARTIAL else ""
    excluded = f", {counts.price_excluded} prices not interpreted" if counts.price_excluded else ""
    return f"{where}: {counts.kept} listings, {counts.priced} priced{excluded}{note}"


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
