"""Pure domain services that turn competitor offers into a pricing position."""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum

from airbnb_tracker.domain.entities import ListingOffer
from airbnb_tracker.domain.exceptions import CurrencyMismatchError, InvalidValueError
from airbnb_tracker.domain.value_objects import Money

_CENTS = Decimal("0.01")


class PricePosition(StrEnum):
    BELOW_MARKET = "below_market"
    COMPETITIVE = "competitive"
    PREMIUM = "premium"


@dataclass(frozen=True, slots=True)
class PriceDistribution:
    """Descriptive statistics of competitor nightly prices."""

    currency: str
    count: int
    minimum: Money
    p25: Money
    median: Money
    p75: Money
    maximum: Money
    mean: Money
    _sorted_amounts: tuple[Decimal, ...] = field(repr=False, compare=False)

    @classmethod
    def from_prices(cls, prices: Sequence[Money]) -> PriceDistribution:
        if not prices:
            raise InvalidValueError("Cannot build a distribution without prices")
        currencies = {price.currency for price in prices}
        if len(currencies) > 1:
            raise CurrencyMismatchError(f"Mixed currencies: {sorted(currencies)}")
        currency = currencies.pop()
        amounts = tuple(sorted(price.amount for price in prices))

        def money(amount: Decimal) -> Money:
            return Money(amount.quantize(_CENTS, ROUND_HALF_UP), currency)

        return cls(
            currency=currency,
            count=len(amounts),
            minimum=money(amounts[0]),
            p25=money(_percentile(amounts, Decimal("0.25"))),
            median=money(_percentile(amounts, Decimal("0.5"))),
            p75=money(_percentile(amounts, Decimal("0.75"))),
            maximum=money(amounts[-1]),
            mean=money(sum(amounts, Decimal(0)) / len(amounts)),
            _sorted_amounts=amounts,
        )

    def percentile_rank(self, price: Money) -> float:
        """Share (0-100) of competitors cheaper than ``price``; ties count as half."""
        self._ensure_currency(price)
        below = sum(1 for amount in self._sorted_amounts if amount < price.amount)
        equal = sum(1 for amount in self._sorted_amounts if amount == price.amount)
        return round((below + equal / 2) / self.count * 100, 1)

    def position_of(self, price: Money) -> PricePosition:
        self._ensure_currency(price)
        if price < self.p25:
            return PricePosition.BELOW_MARKET
        if price > self.p75:
            return PricePosition.PREMIUM
        return PricePosition.COMPETITIVE

    def _ensure_currency(self, price: Money) -> None:
        if price.currency != self.currency:
            raise CurrencyMismatchError(f"{price.currency} vs {self.currency}")


@dataclass(frozen=True, slots=True)
class MarketAnalysis:
    distribution: PriceDistribution | None
    reference_price: Money | None
    position: PricePosition | None
    percentile: float | None


class MarketAnalyzer:
    """Places a reference nightly price within the competitor distribution."""

    def analyze(
        self, competitors: Sequence[ListingOffer], reference_price: Money | None = None
    ) -> MarketAnalysis:
        prices = [o.nightly_price for o in competitors if o.nightly_price is not None]
        distribution = PriceDistribution.from_prices(prices) if prices else None
        if distribution is None or reference_price is None:
            return MarketAnalysis(distribution, reference_price, None, None)
        return MarketAnalysis(
            distribution=distribution,
            reference_price=reference_price,
            position=distribution.position_of(reference_price),
            percentile=distribution.percentile_rank(reference_price),
        )


def availability_rate(offers: Sequence[ListingOffer]) -> float | None:
    """Share (0-100) of offers still bookable; a low value hints at high demand."""
    if not offers:
        return None
    available = sum(1 for offer in offers if offer.is_available)
    return round(available / len(offers) * 100, 1)


def _percentile(sorted_amounts: tuple[Decimal, ...], quantile: Decimal) -> Decimal:
    """Linear interpolation between closest ranks (same as numpy's default)."""
    position = (len(sorted_amounts) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    fraction = position - lower
    return sorted_amounts[lower] + (sorted_amounts[upper] - sorted_amounts[lower]) * fraction


@dataclass(frozen=True, slots=True)
class DiscountStats:
    """How competitors discount a stay length (e.g. monthly discounts for 28+ nights)."""

    median_pct: Decimal | None
    discounted_share_pct: float | None

    @classmethod
    def from_offers(cls, offers: Sequence[ListingOffer]) -> DiscountStats:
        quotes = [o.quote for o in offers if o.quote is not None]
        if not quotes:
            return cls(None, None)
        discounts = [q.discount_pct for q in quotes if q.discount_pct is not None]
        return cls(
            median_pct=statistics.median(discounts) if discounts else None,
            discounted_share_pct=round(len(discounts) / len(quotes) * 100, 1),
        )
