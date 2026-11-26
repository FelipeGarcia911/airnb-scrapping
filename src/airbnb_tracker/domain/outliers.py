"""Price outlier detection: one rule shared by reports, the web UI and exports.

Designed after inspecting real data, where naive cut-offs fail both ways:

* a blocking / long-term price (millions per night, nobody ever booked) must be removed;
* a genuine luxury listing (high price, great rating) must stay;
* an aggressive monthly discount (very low price, great rating) must stay.

So a listing is judged on the median of all its prices, within its own segment (same
bedroom count), and only flagged when it is *both* statistically extreme (robust z-score
on log prices, based on the median and MAD) *and* far from the segment median in
relative terms. Prices many times the segment median are always flagged: as a placeholder
when the listing has no reviews (nobody ever paid it), otherwise as extreme (luxury or a
different kind of property, e.g. a country estate among city flats).
"""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from airbnb_tracker.domain.value_objects import ListingId

_MAD_TO_SIGMA = 0.6745  # makes the MAD comparable to a standard deviation


class OutlierReason(StrEnum):
    PLACEHOLDER = "placeholder_price"  # blocking or long-term price, never a real offer
    EXTREME_HIGH = "extreme_high"
    EXTREME_LOW = "extreme_low"

    @property
    def label(self) -> str:
        return {
            OutlierReason.PLACEHOLDER: "Implausible price, never booked (blocking or "
            "long-term rental)",
            OutlierReason.EXTREME_HIGH: "Far above its segment (luxury or a different kind "
            "of property)",
            OutlierReason.EXTREME_LOW: "Far below its segment (possible price error)",
        }[self]


@dataclass(frozen=True, slots=True)
class PricePoint:
    """A listing's typical nightly price and the segment it competes in."""

    listing_id: ListingId
    nightly: float
    segment: str
    review_count: int = 0


@dataclass(frozen=True, slots=True)
class OutlierFlag:
    listing_id: ListingId
    reason: OutlierReason
    nightly: float
    segment: str
    segment_median: float
    ratio: float  # nightly / segment median
    robust_z: float | None


@dataclass(frozen=True, slots=True)
class OutlierPolicy:
    z_threshold: float = 3.5  # Iglewicz & Hoaglin's usual cut-off for modified z-scores
    high_ratio: float = 2.5
    low_ratio: float = 0.4
    placeholder_ratio: float = 8.0
    min_segment_size: int = 8  # smaller segments are compared with the whole market


class PriceOutlierDetector:
    def __init__(self, policy: OutlierPolicy | None = None) -> None:
        self._policy = policy or OutlierPolicy()

    def detect(self, points: Iterable[PricePoint]) -> dict[ListingId, OutlierFlag]:
        valid = [p for p in points if p.nightly > 0]
        if not valid:
            return {}
        segments: dict[str, list[PricePoint]] = defaultdict(list)
        for point in valid:
            segments[point.segment].append(point)

        flags: dict[ListingId, OutlierFlag] = {}
        for point in valid:
            peers = segments[point.segment]
            segment = point.segment
            if len(peers) < self._policy.min_segment_size:
                peers, segment = valid, "whole market"
            flag = self._judge(point, peers, segment)
            if flag is not None:
                flags[point.listing_id] = flag
        return flags

    def _judge(
        self, point: PricePoint, peers: list[PricePoint], segment: str
    ) -> OutlierFlag | None:
        logs = [math.log(p.nightly) for p in peers]
        center = statistics.median(logs)
        mad = statistics.median(abs(value - center) for value in logs)
        z = _MAD_TO_SIGMA * (math.log(point.nightly) - center) / mad if mad else None
        median_price = math.exp(center)
        ratio = point.nightly / median_price
        policy = self._policy

        reason: OutlierReason | None = None
        if ratio >= policy.placeholder_ratio:
            never_booked = point.review_count == 0
            reason = OutlierReason.PLACEHOLDER if never_booked else OutlierReason.EXTREME_HIGH
        elif z is not None and z >= policy.z_threshold and ratio >= policy.high_ratio:
            reason = OutlierReason.EXTREME_HIGH
        elif z is not None and z <= -policy.z_threshold and ratio <= policy.low_ratio:
            reason = OutlierReason.EXTREME_LOW
        if reason is None:
            return None
        return OutlierFlag(
            listing_id=point.listing_id,
            reason=reason,
            nightly=point.nightly,
            segment=segment,
            segment_median=median_price,
            ratio=ratio,
            robust_z=z,
        )


def segment_of(bedrooms: int | None) -> str:
    return "unknown size" if bedrooms is None else f"{bedrooms} bedrooms"
