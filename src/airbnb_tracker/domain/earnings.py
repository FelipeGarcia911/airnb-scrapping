"""What a booking really earns: Airbnb's fees, taxes and operating costs.

Rules (Airbnb host-only fee model, 2026):

* Length-of-stay discounts apply to the nightly price only: weekly from 7 nights,
  monthly from 28. The cleaning fee is flat and never discounted.
* Airbnb's service fee is a percentage of the booking subtotal (nights + cleaning),
  deducted from the host payout; in some countries VAT is charged on that fee.
* If the host must charge VAT on the stay, the listed price is assumed to include it.

Every rate is a parameter: the defaults are a starting point, not tax advice.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from airbnb_tracker.domain.exceptions import InvalidValueError

WEEKLY_FROM_NIGHTS = 7
MONTHLY_FROM_NIGHTS = 28
DAYS_PER_MONTH = 30


@dataclass(frozen=True, slots=True)
class FeePolicy:
    host_fee_pct: float = 15.5  # Airbnb host-only service fee
    vat_on_fee_pct: float = 19.0  # VAT Airbnb charges on its fee (Colombia: 19%)
    stay_vat_pct: float = 0.0  # VAT you must charge on the stay (0 if not responsible)

    def __post_init__(self) -> None:
        for name in ("host_fee_pct", "vat_on_fee_pct", "stay_vat_pct"):
            if not 0 <= getattr(self, name) < 100:
                raise InvalidValueError(f"{name} must be between 0 and 100")


@dataclass(frozen=True, slots=True)
class StayPricing:
    """How the listing is configured on Airbnb, for one stay length."""

    nightly_price: float
    nights: int
    weekly_discount_pct: float = 0.0
    monthly_discount_pct: float = 0.0
    cleaning_fee: float = 0.0

    def __post_init__(self) -> None:
        if self.nightly_price < 0 or self.cleaning_fee < 0:
            raise InvalidValueError("Prices cannot be negative")
        if self.nights < 1:
            raise InvalidValueError("A stay needs at least one night")

    @property
    def discount_pct(self) -> float:
        if self.nights >= MONTHLY_FROM_NIGHTS:
            return self.monthly_discount_pct
        if self.nights >= WEEKLY_FROM_NIGHTS:
            return self.weekly_discount_pct
        return 0.0

    @property
    def nightly_after_discount(self) -> float:
        return self.nightly_price * (1 - self.discount_pct / 100)


@dataclass(frozen=True, slots=True)
class BookingBreakdown:
    nights: int
    discount_pct: float
    nightly_after_discount: float
    accommodation: float
    cleaning_fee: float
    guest_pays: float
    airbnb_fee: float
    vat_on_fee: float
    payout: float
    stay_vat: float  # part of what the guest pays that you owe as VAT

    @property
    def payout_after_vat(self) -> float:
        return self.payout - self.stay_vat

    @property
    def per_night(self) -> float:
        return self.payout_after_vat / self.nights

    @property
    def effective_fee_pct(self) -> float:
        """Everything that does not reach you, as a share of what the guest pays."""
        if not self.guest_pays:
            return 0.0
        return (self.guest_pays - self.payout_after_vat) / self.guest_pays * 100


def quote_booking(pricing: StayPricing, policy: FeePolicy) -> BookingBreakdown:
    accommodation = pricing.nightly_after_discount * pricing.nights
    subtotal = accommodation + pricing.cleaning_fee
    airbnb_fee = subtotal * policy.host_fee_pct / 100
    vat_on_fee = airbnb_fee * policy.vat_on_fee_pct / 100
    stay_vat = subtotal * policy.stay_vat_pct / (100 + policy.stay_vat_pct)
    return BookingBreakdown(
        nights=pricing.nights,
        discount_pct=pricing.discount_pct,
        nightly_after_discount=pricing.nightly_after_discount,
        accommodation=accommodation,
        cleaning_fee=pricing.cleaning_fee,
        guest_pays=subtotal,
        airbnb_fee=airbnb_fee,
        vat_on_fee=vat_on_fee,
        payout=subtotal - airbnb_fee - vat_on_fee,
        stay_vat=stay_vat,
    )


def nightly_price_for_payout(
    target_per_night: float, template: StayPricing, policy: FeePolicy
) -> float:
    """Listing price that leaves ``target_per_night`` after discount, fees and VAT.

    Payout is linear in the nightly price, so two evaluations solve it exactly.
    """

    def per_night(price: float) -> float:
        return quote_booking(_with_price(template, price), policy).per_night

    base, slope = per_night(0.0), per_night(1.0) - per_night(0.0)
    if slope <= 0:
        raise InvalidValueError("Fees and discounts leave nothing per night")
    return max((target_per_night - base) / slope, 0.0)


@dataclass(frozen=True, slots=True)
class OperatingCosts:
    fixed_monthly: Mapping[str, float] = field(default_factory=dict)  # rent, utilities…
    per_booking: Mapping[str, float] = field(default_factory=dict)  # cleaning, laundry…
    income_tax_pct: float = 0.0  # provision on profit

    @property
    def fixed_total(self) -> float:
        return sum(self.fixed_monthly.values())

    @property
    def per_booking_total(self) -> float:
        return sum(self.per_booking.values())


@dataclass(frozen=True, slots=True)
class MonthlyProjection:
    occupancy_pct: float
    occupied_nights: float
    bookings: float
    guest_payments: float
    airbnb_fees: float  # service fee plus VAT on it
    stay_vat: float
    booking_costs: float
    fixed_costs: float
    income_tax: float
    net_profit: float

    @property
    def margin_pct(self) -> float:
        return self.net_profit / self.guest_payments * 100 if self.guest_payments else 0.0


def project_month(
    pricing: StayPricing, policy: FeePolicy, costs: OperatingCosts, occupancy_pct: float
) -> MonthlyProjection:
    """A typical 30-day month at the given occupancy, with stays of ``pricing.nights``."""
    if not 0 <= occupancy_pct <= 100:
        raise InvalidValueError("occupancy_pct must be between 0 and 100")
    booking = quote_booking(pricing, policy)
    occupied = DAYS_PER_MONTH * occupancy_pct / 100
    bookings = occupied / pricing.nights
    guest_payments = booking.guest_pays * bookings
    fees = (booking.airbnb_fee + booking.vat_on_fee) * bookings
    stay_vat = booking.stay_vat * bookings
    booking_costs = costs.per_booking_total * bookings
    before_tax = guest_payments - fees - stay_vat - booking_costs - costs.fixed_total
    income_tax = max(before_tax, 0.0) * costs.income_tax_pct / 100
    return MonthlyProjection(
        occupancy_pct=occupancy_pct,
        occupied_nights=occupied,
        bookings=bookings,
        guest_payments=guest_payments,
        airbnb_fees=fees,
        stay_vat=stay_vat,
        booking_costs=booking_costs,
        fixed_costs=costs.fixed_total,
        income_tax=income_tax,
        net_profit=before_tax - income_tax,
    )


def break_even_nightly_price(
    template: StayPricing, policy: FeePolicy, costs: OperatingCosts, occupancy_pct: float
) -> float | None:
    """Nightly listing price at which the month covers every cost (profit = 0)."""

    def profit(price: float) -> float:
        return project_month(_with_price(template, price), policy, costs, occupancy_pct).net_profit

    base, slope = profit(0.0), profit(1.0) - profit(0.0)
    if slope <= 0:
        return None
    return max(-base / slope, 0.0)


def _with_price(template: StayPricing, price: float) -> StayPricing:
    return StayPricing(
        nightly_price=price,
        nights=template.nights,
        weekly_discount_pct=template.weekly_discount_pct,
        monthly_discount_pct=template.monthly_discount_pct,
        cleaning_fee=template.cleaning_fee,
    )


# --- base rate vs effective rate -----------------------------------------------------
#
# Competitor prices are captured *effective*: the stay total after their discounts,
# divided by the nights. What you publish on Airbnb is the *base* rate, before your
# discount. Comparing or handing over one as the other double-counts the discount.


def base_for_effective(effective_nightly: float, discount_pct: float) -> float:
    """Base rate to publish so that, after ``discount_pct``, the nightly price is the target."""
    if not 0 <= discount_pct < 100:
        raise InvalidValueError("discount_pct must be between 0 and 100")
    return effective_nightly / (1 - discount_pct / 100)


def discount_for_effective(base_nightly: float, effective_nightly: float) -> float:
    """Discount that turns ``base_nightly`` into ``effective_nightly`` (0 if none needed)."""
    if base_nightly <= 0:
        raise InvalidValueError("base_nightly must be positive")
    return min(max((1 - effective_nightly / base_nightly) * 100, 0.0), 99.0)


# --- margins with paid nights ----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StayEconomics:
    """One stay and the month around it, with ``paid_nights`` actually paid out of 30.

    With a 30-night minimum, gaps between guests usually leave some nights unpaid, so a
    month is *committed* to 30 nights but pays fewer.
    """

    booking: BookingBreakdown
    month: MonthlyProjection
    paid_nights: float
    stay_costs: float  # per-booking costs plus the stay's share of fixed costs

    @property
    def margin_per_stay(self) -> float:
        return self.booking.payout_after_vat - self.stay_costs

    @property
    def margin_per_committed_night(self) -> float:
        return self.month.net_profit / DAYS_PER_MONTH

    @property
    def margin_pct(self) -> float:
        return self.month.margin_pct


def stay_economics(
    pricing: StayPricing, policy: FeePolicy, costs: OperatingCosts, paid_nights: float
) -> StayEconomics:
    if not 0 < paid_nights <= DAYS_PER_MONTH:
        raise InvalidValueError("paid_nights must be between 1 and 30")
    month = project_month(pricing, policy, costs, paid_nights / DAYS_PER_MONTH * 100)
    stays_per_month = paid_nights / pricing.nights
    stay_costs = costs.per_booking_total + costs.fixed_total / stays_per_month
    return StayEconomics(quote_booking(pricing, policy), month, paid_nights, stay_costs)


def base_for_margin(
    target_margin_pct: float,
    template: StayPricing,
    policy: FeePolicy,
    costs: OperatingCosts,
    paid_nights: float,
) -> float | None:
    """Base rate whose month reaches ``target_margin_pct`` (net profit / guest payments).

    Profit and guest payments are both linear in the base rate, so the margin equation
    has a closed-form solution from two evaluations.
    """

    def totals(price: float) -> tuple[float, float]:
        month = stay_economics(_with_price(template, price), policy, costs, paid_nights).month
        return month.net_profit, month.guest_payments

    (profit0, guest0), (profit1, guest1) = totals(0.0), totals(1.0)
    margin = target_margin_pct / 100
    slope = (profit1 - profit0) - margin * (guest1 - guest0)
    if slope <= 0:
        return None
    return max((margin * guest0 - profit0) / slope, 0.0)
