"""Use case: price percentiles per stay length, discounts, availability and your position."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, replace

from airbnb_tracker.application.dto import DurationReport, ReportBundle
from airbnb_tracker.application.ports import CalendarRepository, Clock, SnapshotRepository
from airbnb_tracker.application.use_cases.price_outliers import (
    comparable_only,
    detect_price_outliers,
)
from airbnb_tracker.domain.entities import ListingOffer, Snapshot
from airbnb_tracker.domain.market_analysis import DiscountStats, MarketAnalyzer
from airbnb_tracker.domain.outliers import OutlierPolicy, PriceOutlierDetector
from airbnb_tracker.domain.profile import AvailabilitySummary
from airbnb_tracker.domain.property_types import PropertyGroup
from airbnb_tracker.domain.value_objects import ListingId, Money, StayPeriod


@dataclass(frozen=True, slots=True)
class BuildReportQuery:
    stays: Sequence[StayPeriod]
    conditions_key: str
    my_listing_id: ListingId | None = None
    my_nightly_price: Money | None = None
    availability_window_days: int = 60
    exclude_outliers: bool = True  # leave implausible prices out of every statistic
    outlier_policy: OutlierPolicy | None = None
    property_groups: frozenset[PropertyGroup] | None = None  # comp set: same kind of property


class BuildMarketReportUseCase:
    def __init__(
        self,
        snapshots: SnapshotRepository,
        calendars: CalendarRepository,
        clock: Clock,
        analyzer: MarketAnalyzer | None = None,
    ) -> None:
        self._snapshots = snapshots
        self._calendars = calendars
        self._clock = clock
        self._analyzer = analyzer or MarketAnalyzer()

    def execute(self, query: BuildReportQuery) -> ReportBundle:
        found = (self._snapshots.latest(stay, query.conditions_key) for stay in query.stays)
        latest = [snapshot for snapshot in found if snapshot is not None]
        latest = comparable_only(latest, query.property_groups, keep=query.my_listing_id)
        outliers = detect_price_outliers(latest, PriceOutlierDetector(query.outlier_policy))
        if query.exclude_outliers and outliers:
            latest = [_without(snapshot, set(outliers)) for snapshot in latest]
        by_nights: dict[int, list[Snapshot]] = defaultdict(list)
        for snapshot in latest:
            by_nights[snapshot.stay.nights].append(snapshot)

        sample = {offer.listing.id for snapshot in latest for offer in snapshot.offers}
        calendars = self._calendars.latest_calendars()
        mine = calendars.pop(query.my_listing_id, None) if query.my_listing_id else None
        calendars = {i: c for i, c in calendars.items() if i in sample}  # current sample only
        window = query.availability_window_days
        return ReportBundle(
            generated_at=self._clock.now(),
            data_captured_at=max((s.captured_at for s in latest), default=None),
            durations=tuple(
                self._duration_report(nights, by_nights[nights], query)
                for nights in sorted(by_nights)
            ),
            availability=AvailabilitySummary.from_calendars(list(calendars.values()), window),
            availability_window_days=window,
            my_unavailable_pct=mine.unavailable_pct(window) if mine else None,
            my_min_nights=mine.typical_min_nights(window) if mine else None,
            outliers=tuple(outliers.values()),
            outliers_excluded=query.exclude_outliers,
        )

    def _duration_report(
        self, nights: int, snapshots: Sequence[Snapshot], query: BuildReportQuery
    ) -> DurationReport:
        competitors: list[ListingOffer] = [
            offer
            for snapshot in snapshots
            for offer in snapshot.offers
            if offer.listing.id != query.my_listing_id
        ]
        analysis = self._analyzer.analyze(competitors, query.my_nightly_price)
        discounts = DiscountStats.from_offers(competitors)
        priced = [offer for offer in competitors if offer.quote is not None]
        return DurationReport(
            nights=nights,
            check_ins=len(snapshots),
            observations=len(priced),  # only what the percentiles are computed from
            listings=len({o.listing.id for o in priced}),
            distribution=analysis.distribution,
            median_discount_pct=discounts.median_pct,
            discounted_share_pct=discounts.discounted_share_pct,
            my_nightly=query.my_nightly_price,
            position=analysis.position,
            percentile=analysis.percentile,
        )


def _without(snapshot: Snapshot, excluded: set[ListingId]) -> Snapshot:
    offers = tuple(o for o in snapshot.offers if o.listing.id not in excluded)
    return replace(snapshot, offers=offers)
