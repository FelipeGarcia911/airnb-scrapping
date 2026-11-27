"""A change in the median between stay lengths: different prices or a different sample?"""

from __future__ import annotations

import pandas as pd
import pytest

from airbnb_tracker.interface.web.analytics import paired_change


def rows(nights: int, prices: dict[str, float]) -> list[dict[str, object]]:
    return [
        {"listing_id": listing, "check_in": "2027-01-15", "nights": nights, "nightly_price": price}
        for listing, price in prices.items()
    ]


def test_sample_effect_is_separated_from_the_price_change() -> None:
    # The same two listings drop 10% at 30 nights, but a cheap listing only shows up at
    # 7 nights and an expensive one only at 30: the plain medians barely move.
    latest = pd.DataFrame(
        rows(7, {"a": 100.0, "b": 200.0, "cheap": 50.0})
        + rows(30, {"a": 90.0, "b": 180.0, "pricey": 400.0})
    )
    change = paired_change(latest, 7, 30)
    assert (change.listings, change.pairs) == (2, 2)
    assert change.change_pct == pytest.approx(-10.0)
    assert change.unpaired_change_pct == pytest.approx(80.0)  # median 100 -> 180
    assert change.sample_effect_pct == pytest.approx(90.0)


def test_no_matched_listings_means_no_paired_change() -> None:
    latest = pd.DataFrame(rows(7, {"a": 100.0}) + rows(30, {"b": 90.0}))
    change = paired_change(latest, 7, 30)
    assert change.listings == 0 and change.change_pct is None and change.sample_effect_pct is None
