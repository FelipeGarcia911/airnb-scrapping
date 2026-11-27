"""General financial settings (fees, costs, paid nights), shared by the pricing pages.

They live in the ``calculator`` section of ``config.yaml`` and are edited in the
Earnings calculator; every other page reads them from here.
"""

from __future__ import annotations

from typing import Any

from airbnb_tracker.domain.earnings import FeePolicy, OperatingCosts
from airbnb_tracker.infrastructure.config import ConfigurationError, read_raw_config
from airbnb_tracker.interface.web.context import config_path

SECTION = "calculator"
DEFAULT_PAID_NIGHTS = 20  # paid nights in a 30-day month: gaps between long stays


def saved_inputs() -> dict[str, Any]:
    try:
        section = read_raw_config(config_path()).get(SECTION)
    except ConfigurationError:
        return {}
    return dict(section) if isinstance(section, dict) else {}


def fee_policy(saved: dict[str, Any]) -> FeePolicy:
    return FeePolicy(
        host_fee_pct=float(saved.get("host_fee_pct", 15.5)),
        vat_on_fee_pct=float(saved.get("vat_on_fee_pct", 19.0)),
        stay_vat_pct=float(saved.get("stay_vat_pct", 0.0)),
    )


def operating_costs(saved: dict[str, Any]) -> OperatingCosts:
    def amounts(key: str) -> dict[str, float]:
        values = saved.get(key) or {}
        return (
            {str(k): float(v or 0) for k, v in values.items()} if isinstance(values, dict) else {}
        )

    return OperatingCosts(
        fixed_monthly=amounts("fixed_monthly"),
        per_booking=amounts("per_booking"),
        income_tax_pct=float(saved.get("income_tax_pct", 0.0)),
    )


def paid_nights(saved: dict[str, Any]) -> int:
    return int(saved.get("paid_nights_per_month", DEFAULT_PAID_NIGHTS))
