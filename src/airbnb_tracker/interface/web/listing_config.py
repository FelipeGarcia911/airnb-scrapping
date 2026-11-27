"""Settings → Listing configuration: your pricing on Airbnb and Airbnb's fees.

Pure translation between ``config.yaml`` and the form (no Streamlit here). The values
live where the rest of the app already reads them: rate, discounts, cleaning fee and
minimum stay in ``my_listing``; fees and taxes in the ``calculator`` section.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

from airbnb_tracker.interface.web.finance_settings import SECTION as FINANCE_SECTION

STAY_VAT_PCT = 19.0  # Colombian VAT when you are "responsable de IVA"


@dataclass(frozen=True)
class ListingConfiguration:
    nightly_price: int | None = None  # base rate you publish, before discounts
    weekly_discount_pct: int = 0
    monthly_discount_pct: int = 0
    cleaning_fee: int | None = None
    min_nights: int | None = None
    host_fee_pct: float = 15.5
    vat_on_fee_pct: float = 19.0
    charges_stay_vat: bool = False

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> ListingConfiguration:
        mine = raw.get("my_listing") or {}
        finance = raw.get(FINANCE_SECTION) or {}
        defaults = cls()
        return cls(
            nightly_price=_opt_int(mine.get("nightly_price")),
            weekly_discount_pct=round(float(mine.get("weekly_discount_pct") or 0)),
            monthly_discount_pct=round(float(mine.get("monthly_discount_pct") or 0)),
            cleaning_fee=_opt_int(mine.get("cleaning_fee")),
            min_nights=_opt_int(mine.get("min_nights")) or None,
            host_fee_pct=float(finance.get("host_fee_pct", defaults.host_fee_pct)),
            vat_on_fee_pct=float(finance.get("vat_on_fee_pct", defaults.vat_on_fee_pct)),
            charges_stay_vat=bool(finance.get("stay_vat_pct", 0)),
        )

    def apply_to(self, raw: dict[str, Any]) -> dict[str, Any]:
        """A copy of ``raw`` with these values; every other key is kept."""
        updated = copy.deepcopy(raw)
        updated.setdefault("my_listing", {}).update(
            {
                "nightly_price": self.nightly_price or None,
                "weekly_discount_pct": self.weekly_discount_pct,
                "monthly_discount_pct": self.monthly_discount_pct,
                "cleaning_fee": self.cleaning_fee,
                "min_nights": self.min_nights or None,
            }
        )
        finance = updated.get(FINANCE_SECTION)
        updated[FINANCE_SECTION] = {
            **(finance if isinstance(finance, dict) else {}),
            "host_fee_pct": self.host_fee_pct,
            "vat_on_fee_pct": self.vat_on_fee_pct,
            "stay_vat_pct": STAY_VAT_PCT if self.charges_stay_vat else 0.0,
        }
        return updated


def _opt_int(value: Any) -> int | None:
    return None if value is None else round(float(value))
