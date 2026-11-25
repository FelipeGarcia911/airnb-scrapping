"""Use case: search the listings that match your filters and store what they charge."""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from airbnb_tracker.application.dto import SearchCriteria, SearchFilters
from airbnb_tracker.application.exceptions import GatewayError
from airbnb_tracker.application.ports import Clock, ListingSearchGateway, SnapshotRepository
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
    competitor_filter: ListingSpecification = field(default_factory=AnyListing)

    def __post_init__(self) -> None:
        if self.center is None and self.template_url is None:
            raise ValueError("ScanMarketCommand needs a center or a template_url")


@dataclass(frozen=True, slots=True)
class ScanSummary:
    stay: StayPeriod
    found: int
    kept: int
    error: str | None = None


class ScanMarketUseCase:
    def __init__(
        self, gateway: ListingSearchGateway, repository: SnapshotRepository, clock: Clock
    ) -> None:
        self._gateway = gateway
        self._repository = repository
        self._clock = clock

    def execute(self, command: ScanMarketCommand) -> list[ScanSummary]:
        area, is_competitor = self._area_and_rule(command)
        return [self._scan(command, stay, area, is_competitor) for stay in command.stays]

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
        command: ScanMarketCommand,
        stay: StayPeriod,
        area: BoundingBox | None,
        is_competitor: ListingSpecification,
    ) -> ScanSummary:
        criteria = SearchCriteria(
            stay=stay,
            currency=command.currency,
            filters=command.filters,
            area=area,
            template_url=command.template_url,
        )
        try:
            offers = self._gateway.search(criteria)
        except GatewayError as error:
            logger.warning("Market scan failed for %s: %s", stay, error)
            return ScanSummary(stay, found=0, kept=0, error=str(error))

        competitors = _unique(o for o in offers if is_competitor.is_satisfied_by(o.listing))
        self._repository.add(
            Snapshot(
                captured_at=self._clock.now(),
                stay=stay,
                currency=command.currency,
                guests=command.filters.guests,
                offers=tuple(competitors),
            )
        )
        return ScanSummary(stay, found=len(offers), kept=len(competitors))


def _unique(offers: Iterable[ListingOffer]) -> list[ListingOffer]:
    seen: dict[ListingId, ListingOffer] = {}
    for offer in offers:
        seen.setdefault(offer.listing.id, offer)
    return list(seen.values())
