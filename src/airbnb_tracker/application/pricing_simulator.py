"""Pricing scenarios for one stay: current, aligned with the market, and a target margin.

The admin keeps either the base rate or the discount fixed and the other one is solved,
so every scenario is a configuration that can be entered in Airbnb as is.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from airbnb_tracker.domain.earnings import (
    MONTHLY_FROM_NIGHTS,
    WEEKLY_FROM_NIGHTS,
    FeePolicy,
    OperatingCosts,
    StayEconomics,
    StayPricing,
    base_for_effective,
    base_for_margin,
    discount_for_effective,
    stay_economics,
)


class Keep(StrEnum):
    BASE = "base"  # keep the base rate, adjust the discount
    DISCOUNT = "discount"  # keep the discount, adjust the base rate


@dataclass(frozen=True, slots=True)
class CurrentSetup:
    base_nightly: float
    weekly_discount_pct: float
    monthly_discount_pct: float
    cleaning_fee: float


@dataclass(frozen=True, slots=True)
class Scenario:
    name: str
    pricing: StayPricing
    economics: StayEconomics

    @property
    def nightly_after_discount(self) -> float:
        return self.pricing.nightly_after_discount


@dataclass(frozen=True, slots=True)
class SimulationInputs:
    current: CurrentSetup
    nights: int
    paid_nights: float  # paid nights per 30-day month
    policy: FeePolicy
    costs: OperatingCosts
    keep: Keep


def discount_kind(nights: int) -> str | None:
    if nights >= MONTHLY_FROM_NIGHTS:
        return "monthly"
    if nights >= WEEKLY_FROM_NIGHTS:
        return "weekly"
    return None


def current_scenario(inputs: SimulationInputs) -> Scenario:
    return _scenario("Current", inputs, inputs.current.base_nightly, _current_discount(inputs))


def aligned_scenario(inputs: SimulationInputs, target_nightly: float, name: str) -> Scenario:
    """Configuration whose nightly price after discount equals ``target_nightly``."""
    if inputs.keep is Keep.BASE and discount_kind(inputs.nights):
        discount = discount_for_effective(inputs.current.base_nightly, target_nightly)
        return _scenario(name, inputs, inputs.current.base_nightly, discount)
    discount = _current_discount(inputs)
    return _scenario(name, inputs, base_for_effective(target_nightly, discount), discount)


def margin_scenario(inputs: SimulationInputs, target_margin_pct: float) -> Scenario | None:
    """Lowest configuration that still keeps the 30-day month at the target net margin."""
    discount = _current_discount(inputs)
    template = _pricing(inputs, 1.0, discount)
    base = base_for_margin(
        target_margin_pct, template, inputs.policy, inputs.costs, inputs.paid_nights
    )
    if base is None:
        return None
    target_nightly = base * (1 - discount / 100)
    return aligned_scenario(inputs, target_nightly, f"Floor for {target_margin_pct:g}% margin")


def _current_discount(inputs: SimulationInputs) -> float:
    kind = discount_kind(inputs.nights)
    if kind == "monthly":
        return inputs.current.monthly_discount_pct
    if kind == "weekly":
        return inputs.current.weekly_discount_pct
    return 0.0


def _pricing(inputs: SimulationInputs, base: float, discount: float) -> StayPricing:
    kind = discount_kind(inputs.nights)
    return StayPricing(
        nightly_price=base,
        nights=inputs.nights,
        weekly_discount_pct=discount if kind == "weekly" else inputs.current.weekly_discount_pct,
        monthly_discount_pct=(
            discount if kind == "monthly" else inputs.current.monthly_discount_pct
        ),
        cleaning_fee=inputs.current.cleaning_fee,
    )


def _scenario(name: str, inputs: SimulationInputs, base: float, discount: float) -> Scenario:
    pricing = _pricing(inputs, base, discount)
    economics = stay_economics(pricing, inputs.policy, inputs.costs, inputs.paid_nights)
    return Scenario(name, pricing, economics)
