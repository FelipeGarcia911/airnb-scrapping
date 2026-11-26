"""Apply the domain outlier rule to collected data."""

from __future__ import annotations

import statistics
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import replace

from airbnb_tracker.domain.entities import Listing, Snapshot
from airbnb_tracker.domain.outliers import (
    OutlierFlag,
    PriceOutlierDetector,
    PricePoint,
    segment_of,
)
from airbnb_tracker.domain.property_types import PropertyGroup, property_group
from airbnb_tracker.domain.value_objects import ListingId, StayPeriod


def latest_per_stay(snapshots: Sequence[Snapshot]) -> list[Snapshot]:
    newest: dict[StayPeriod, Snapshot] = {}
    for snapshot in snapshots:
        current = newest.get(snapshot.stay)
        if current is None or snapshot.captured_at > current.captured_at:
            newest[snapshot.stay] = snapshot
    return list(newest.values())


def detect_price_outliers(
    snapshots: Sequence[Snapshot], detector: PriceOutlierDetector | None = None
) -> dict[ListingId, OutlierFlag]:
    """Judge every listing on the median nightly price of the latest search per stay."""
    prices: dict[ListingId, list[float]] = defaultdict(list)
    listings: dict[ListingId, Listing] = {}
    for snapshot in latest_per_stay(snapshots):
        for offer in snapshot.offers:
            if offer.nightly_price is not None:
                prices[offer.listing.id].append(float(offer.nightly_price.amount))
                listings[offer.listing.id] = offer.listing
    points = [
        PricePoint(
            listing_id=listing_id,
            nightly=statistics.median(values),
            segment=segment_of(listings[listing_id].bedrooms),
            review_count=listings[listing_id].review_count,
        )
        for listing_id, values in prices.items()
    ]
    return (detector or PriceOutlierDetector()).detect(points)


def comparable_only(
    snapshots: Sequence[Snapshot],
    groups: frozenset[PropertyGroup] | None,
    keep: ListingId | None = None,
) -> list[Snapshot]:
    """Keep listings of the given property groups (and ``keep``, usually yours)."""
    if not groups:
        return list(snapshots)
    return [
        replace(
            snapshot,
            offers=tuple(
                o
                for o in snapshot.offers
                if o.listing.id == keep or property_group(o.listing.property_type) in groups
            ),
        )
        for snapshot in snapshots
    ]
