from datetime import date, timedelta
from decimal import Decimal

import pytest
from tests.conftest import NOW, STAY, make_listing, make_offer, make_quote, make_snapshot

from airbnb_tracker.domain.entities import ListingOffer
from airbnb_tracker.domain.exceptions import CurrencyMismatchError, InvalidValueError
from airbnb_tracker.domain.market_analysis import (
    DiscountStats,
    MarketAnalyzer,
    PriceDistribution,
    PricePosition,
)
from airbnb_tracker.domain.profile import (
    AvailabilitySummary,
    CalendarDay,
    CalendarSnapshot,
)
from airbnb_tracker.domain.stay_planner import plan_stays
from airbnb_tracker.domain.value_objects import ListingId, Money, StayPeriod


def cop(amount: float) -> Money:
    return Money.of(amount, "COP")


class TestEntities:
    def test_quote_nightly_and_discount(self) -> None:
        quote = make_quote(nightly=90_000, original_nightly=100_000)
        assert quote.nightly == cop(90_000)
        assert quote.discount_pct == Decimal("10.0")

    def test_offer_rejects_quote_of_other_listing(self) -> None:
        with pytest.raises(InvalidValueError):
            ListingOffer(make_listing("1"), make_quote("2"))

    def test_snapshot_rejects_duplicates_and_foreign_stays(self) -> None:
        with pytest.raises(InvalidValueError):
            make_snapshot(make_offer("1"), make_offer("1"))
        other = StayPeriod(date(2027, 2, 1), date(2027, 2, 3))
        with pytest.raises(InvalidValueError):
            make_snapshot(make_offer("1", stay=other))

    def test_snapshot_search_position_is_one_based(self) -> None:
        snapshot = make_snapshot(make_offer("7"), make_offer("8"))
        assert snapshot.search_position(ListingId("8")) == 2
        assert snapshot.search_position(ListingId("9")) is None


class TestPriceDistribution:
    def test_quartiles_use_linear_interpolation(self) -> None:
        dist = PriceDistribution.from_prices([cop(v) for v in (10, 20, 30, 40, 50)])
        assert (dist.p25, dist.median, dist.p75) == (cop(20), cop(30), cop(40))
        assert dist.mean == cop(30)

    def test_rejects_empty_and_mixed_currency(self) -> None:
        with pytest.raises(InvalidValueError):
            PriceDistribution.from_prices([])
        with pytest.raises(CurrencyMismatchError):
            PriceDistribution.from_prices([cop(1), Money.of(1, "USD")])

    def test_percentile_rank_counts_ties_as_half(self) -> None:
        dist = PriceDistribution.from_prices([cop(v) for v in (10, 20, 30, 40)])
        assert dist.percentile_rank(cop(25)) == 50.0
        assert dist.percentile_rank(cop(20)) == 37.5

    @pytest.mark.parametrize(
        ("price", "position"),
        [
            (15, PricePosition.BELOW_MARKET),
            (30, PricePosition.COMPETITIVE),
            (60, PricePosition.PREMIUM),
        ],
    )
    def test_position(self, price: int, position: PricePosition) -> None:
        dist = PriceDistribution.from_prices([cop(v) for v in (10, 20, 30, 40, 50)])
        assert dist.position_of(cop(price)) is position


def test_analyzer_ignores_offers_without_price() -> None:
    offers = [make_offer("1", 40), make_offer("2", 60), make_offer("3", None)]
    analysis = MarketAnalyzer().analyze(offers, cop(55))
    assert analysis.distribution is not None
    assert analysis.distribution.count == 2
    assert analysis.percentile == 50.0


def test_discount_stats() -> None:
    offers = [make_offer("1", 90, original_nightly=100), make_offer("2", 80), make_offer("3")]
    stats = DiscountStats.from_offers(offers)
    assert stats.median_pct == Decimal("10.0")
    assert stats.discounted_share_pct == 33.3
    assert DiscountStats.from_offers([]) == DiscountStats(None, None)


class TestCalendar:
    def _calendar(self, pattern: str, min_nights: list[int] | None = None) -> CalendarSnapshot:
        start = NOW.date()
        nights = min_nights or [2] * len(pattern)
        days = tuple(
            CalendarDay(start + timedelta(days=i), flag == "A", nights[i])
            for i, flag in enumerate(pattern)
        )
        return CalendarSnapshot(ListingId("1"), NOW, days)

    def test_unavailable_pct_only_counts_upcoming_window(self) -> None:
        calendar = self._calendar("AXXXAAAAAA")
        assert calendar.unavailable_pct(window_days=4) == 75.0
        assert calendar.unavailable_pct(window_days=10) == 30.0

    def test_typical_min_nights_is_the_mode(self) -> None:
        calendar = self._calendar("AAAA", min_nights=[3, 3, 7, 3])
        assert calendar.typical_min_nights(window_days=10) == 3

    def test_summary_uses_medians(self) -> None:
        summary = AvailabilitySummary.from_calendars(
            [self._calendar("AAXX"), self._calendar("XXXX"), self._calendar("AAAA")], 4
        )
        assert summary.listings_measured == 3
        assert summary.median_unavailable_pct == 50.0
        assert summary.median_min_nights == 2

    def test_rejects_repeated_days(self) -> None:
        day = CalendarDay(NOW.date(), True)
        with pytest.raises(InvalidValueError):
            CalendarSnapshot(ListingId("1"), NOW, (day, day))


def test_plan_stays_crosses_durations_and_check_ins() -> None:
    stays = plan_stays(date(2026, 11, 25), [7, 30], first_check_in_days=7, every_days=30,
                       horizon_days=40)  # fmt: skip
    assert [(s.check_in, s.nights) for s in stays] == [
        (date(2026, 12, 2), 7),
        (date(2026, 12, 2), 30),
        (date(2027, 1, 1), 7),
        (date(2027, 1, 1), 30),
    ]


def test_stay_fixture_is_a_week() -> None:
    assert STAY.nights == 7
