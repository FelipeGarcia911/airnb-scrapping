from datetime import date
from decimal import Decimal

import pytest

from airbnb_tracker.domain.exceptions import CurrencyMismatchError, InvalidValueError
from airbnb_tracker.domain.value_objects import (
    BoundingBox,
    GeoPoint,
    ListingId,
    Money,
    StayPeriod,
)


class TestMoney:
    def test_normalises_currency_and_amount(self) -> None:
        money = Money.of(10.5, "usd")
        assert money.amount == Decimal("10.5")
        assert money.currency == "USD"

    @pytest.mark.parametrize("amount", ["-1", "NaN"])
    def test_rejects_invalid_amounts(self, amount: str) -> None:
        with pytest.raises(InvalidValueError):
            Money(Decimal(amount), "USD")

    def test_rejects_invalid_currency(self) -> None:
        with pytest.raises(InvalidValueError):
            Money.of(1, "US")

    def test_divided_by_rounds_to_cents(self) -> None:
        assert Money.of(100, "USD").divided_by(3) == Money.of("33.33", "USD")

    def test_cannot_compare_different_currencies(self) -> None:
        with pytest.raises(CurrencyMismatchError):
            _ = Money.of(1, "USD") < Money.of(1, "COP")

    def test_ordering(self) -> None:
        assert Money.of(1, "USD") < Money.of(2, "USD") <= Money.of(2, "USD")

    def test_discount_from(self) -> None:
        assert Money.of(90, "USD").discount_from(Money.of(100, "USD")) == Decimal("10.0")
        assert Money.of(100, "USD").discount_from(Money.of(90, "USD")) is None

    def test_str(self) -> None:
        assert str(Money.of(1234.5, "USD")) == "1,234.50 USD"


class TestStayPeriod:
    def test_nights(self) -> None:
        assert StayPeriod.starting(date(2027, 1, 15), 3).check_out == date(2027, 1, 18)
        assert StayPeriod(date(2027, 1, 15), date(2027, 1, 18)).nights == 3

    def test_rejects_non_positive_length(self) -> None:
        with pytest.raises(InvalidValueError):
            StayPeriod(date(2027, 1, 15), date(2027, 1, 15))


class TestGeo:
    def test_distance_between_known_points(self) -> None:
        # Parque Lleras -> Parque Poblado (Medellín) is roughly 0.6 km
        distance = GeoPoint(6.2087, -75.5677).distance_km_to(GeoPoint(6.2103, -75.5714))
        assert 0.4 < distance < 0.6

    def test_bounding_box_contains_radius(self) -> None:
        center = GeoPoint(6.2, -75.56)
        box = BoundingBox.around(center, 1.0)
        north = GeoPoint(box.north_east.latitude, center.longitude)
        assert center.distance_km_to(north) == pytest.approx(1.0, rel=0.01)

    def test_rejects_out_of_range_latitude(self) -> None:
        with pytest.raises(InvalidValueError):
            GeoPoint(91, 0)


class TestListingId:
    def test_accepts_numeric_text(self) -> None:
        assert str(ListingId(" 969805694560270133 ")) == "969805694560270133"

    def test_rejects_urls(self) -> None:
        with pytest.raises(InvalidValueError):
            ListingId("https://airbnb.com/rooms/1")
