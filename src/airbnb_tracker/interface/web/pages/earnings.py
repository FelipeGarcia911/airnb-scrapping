"""Earnings calculator: how to configure Airbnb, what each booking leaves and the month's P&L."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import altair as alt
import pandas as pd
import streamlit as st

from airbnb_tracker.domain.earnings import (
    MONTHLY_FROM_NIGHTS,
    WEEKLY_FROM_NIGHTS,
    BookingBreakdown,
    FeePolicy,
    MonthlyProjection,
    OperatingCosts,
    StayPricing,
    break_even_nightly_price,
    nightly_price_for_payout,
    project_month,
    quote_booking,
)
from airbnb_tracker.domain.exceptions import InvalidValueError
from airbnb_tracker.infrastructure.config import AppSettings, read_raw_config, save_raw_config
from airbnb_tracker.interface.web.components import caveat, how_to_read, insight, section
from airbnb_tracker.interface.web.context import config_path
from airbnb_tracker.interface.web.data import MarketData, load_market
from airbnb_tracker.interface.web.formatting import format_money, money_input
from airbnb_tracker.interface.web.guards import require_settings
from airbnb_tracker.interface.web.navigation import next_step
from airbnb_tracker.interface.web.theme import CORAL, INK

CALCULATOR_PRICE = "_calc_nightly_price"
CALCULATOR_NIGHTS = "_calc_nights"
_RAW_SECTION = "calculator"  # UI-only section of config.yaml with your saved costs
_STAY_CHOICES = (1, 3, 7, 14, 20, 30, 60, 90)

DEFAULT_FIXED = {
    "Building fee (administración)": 0,
    "Utilities (energy, water, gas)": 0,
    "Internet & TV": 0,
    "Property tax, per month": 0,
    "Insurance": 0,
    "Maintenance & repairs": 0,
    "Supplies (toiletries, coffee…)": 0,
}
DEFAULT_PER_BOOKING = {"Cleaning": 0, "Laundry": 0, "Welcome amenities": 0}

_AIRBNB_FEES_URL = (
    "https://www.airbnb.com/resources/hosting-homes/a/how-much-does-airbnb-charge-hosts-288"
)
_AIRBNB_RESULTS_URL = (
    "https://www.sec.gov/Archives/edgar/data/1559720/000119312526048670/d58192dex991.htm"
)
_RENTEO_URL = "https://www.renteo.com.co/blog/cuanto-cobra-airbnb-comision-colombia"
_TAX_GUIDE_URL = "https://assets.airbnb.com/help/Airbnb_TaxGuide2026_Colombia_SPANISH.pdf"
_DISCOUNTS_URL = "https://www.airbnb.com/help/article/2061"
_FEE_SOURCES = (
    "- **15.5% host-only service fee**, deducted from your payout, on nights and cleaning: "
    f"[Airbnb]({_AIRBNB_FEES_URL}), [Airbnb Q4 2025 results]({_AIRBNB_RESULTS_URL}).\n"
    f"- **19% VAT on Airbnb's fee in Colombia**: [Renteo]({_RENTEO_URL}).\n"
    "- **19% VAT on the stay** only if you are *responsable de IVA*: "
    f"[Airbnb tax guide Colombia 2026]({_TAX_GUIDE_URL}).\n"
    "- **Discounts** apply to nights only (weekly from 7, monthly from 28 nights), never to "
    f"the cleaning fee: [Airbnb help]({_DISCOUNTS_URL})."
)


def render() -> None:
    st.title("Earnings calculator")
    st.caption("Step 4 of 5 · Pricing & finances")
    st.markdown(
        "Set a price and see **what you really earn**: Airbnb's fees, taxes and your costs. "
        "It also tells you **what to enter in Airbnb** (price, discounts, cleaning fee)."
    )
    settings = require_settings()
    if settings is None:
        return
    currency = settings.scraping.currency
    saved = _saved_inputs()
    market = load_market(settings, None)

    policy = _fees_section(saved)
    pricing = _pricing_section(settings, saved, market, policy)
    if pricing is None:
        return
    booking = quote_booking(pricing, policy)
    _booking_result(booking, currency)

    costs, occupancy = _costs_section(saved, currency, market)
    month = project_month(pricing, policy, costs, occupancy)
    _month_result(
        month, pricing=pricing, policy=policy, costs=costs, currency=currency, market=market
    )
    _airbnb_setup(pricing, currency)
    _save_defaults(pricing, policy, costs, occupancy)
    next_step("export", "Next: export the data")


# --- inputs --------------------------------------------------------------------------


def _pricing_section(
    settings: AppSettings, saved: dict[str, Any], market: MarketData | None, policy: FeePolicy
) -> StayPricing | None:
    currency = settings.scraping.currency
    weekly_typical, monthly_typical = _market_discounts(market)
    with st.container(border=True):
        st.subheader("1 · Your Airbnb pricing")
        st.caption("The values you set in Airbnb → Calendar → Pricing.")
        reverse = st.toggle(
            "Start from what I want to receive per night",
            help="Enter your target net income per night and get the price to publish.",
        )
        c1, c2, c3, c4 = st.columns(4)
        st.session_state.setdefault(CALCULATOR_NIGHTS, int(saved.get("nights", 30)))
        nights = c1.selectbox(
            "Stay length to simulate",
            _STAY_CHOICES,
            key=CALCULATOR_NIGHTS,
            format_func=lambda n: f"{n} night{'s' if n > 1 else ''}",
        )
        with c2:
            cleaning = money_input(
                "Cleaning fee per stay",
                key="_calc_cleaning",
                currency=currency,
                default=saved.get("cleaning_fee"),
            )
        weekly = c3.number_input(
            "Weekly discount %",
            0,
            99,
            int(saved.get("weekly_discount_pct", weekly_typical or 0)),
            help=f"From {WEEKLY_FROM_NIGHTS} nights. Market typical: {_pct(weekly_typical)}.",
        )
        monthly = c4.number_input(
            "Monthly discount %",
            0,
            99,
            int(saved.get("monthly_discount_pct", monthly_typical or 0)),
            help=f"From {MONTHLY_FROM_NIGHTS} nights. Market typical: {_pct(monthly_typical)}.",
        )
        if weekly_typical or monthly_typical:
            st.caption(
                f"Market typical discounts: weekly {_pct(weekly_typical)}, monthly "
                f"{_pct(monthly_typical)} (median of competitors that discount)."
            )

        template = StayPricing(1.0, int(nights), weekly, monthly, float(cleaning or 0))
        if reverse:
            target = money_input(
                "I want to receive per night",
                key="_calc_target",
                currency=currency,
                default=saved.get("target_per_night"),
            )
            if not target:
                st.info("Enter how much you want to receive per night.")
                return None
            try:
                price = nightly_price_for_payout(float(target), template, policy)
            except InvalidValueError as error:
                st.error(str(error))
                return None
            insight(
                f"Publish **{format_money(price, currency)}** per night to receive "
                f"**{format_money(target, currency)}** per night on {nights}-night stays, after "
                "discounts, Airbnb's fee and taxes."
            )
        else:
            default_price = saved.get("nightly_price") or _my_price(settings)
            price_input = money_input(
                "Nightly price you publish",
                key=CALCULATOR_PRICE,
                currency=currency,
                default=default_price,
            )
            if not price_input:
                st.info("Enter the nightly price you publish (or plan to) on Airbnb.")
                return None
            price = float(price_input)
    return StayPricing(price, int(nights), weekly, monthly, float(cleaning or 0))


def _fees_section(saved: dict[str, Any]) -> FeePolicy:
    with st.expander("Airbnb fees and taxes (Colombia defaults)", icon=":material/percent:"):
        c1, c2, c3 = st.columns(3)
        host_fee = c1.number_input(
            "Airbnb service fee %", 0.0, 50.0, float(saved.get("host_fee_pct", 15.5)), step=0.5
        )
        vat_on_fee = c2.number_input(
            "VAT on Airbnb's fee %", 0.0, 30.0, float(saved.get("vat_on_fee_pct", 19.0)), step=1.0
        )
        responsible = c3.toggle(
            "I charge VAT on the stay (responsable de IVA)",
            value=bool(saved.get("stay_vat_pct", 0)),
            help="If so, 19% of what the guest pays is VAT you owe the DIAN.",
        )
        st.markdown(_FEE_SOURCES)
        caveat("Rates change and depend on your tax situation: confirm them with your accountant.")
    return FeePolicy(host_fee, vat_on_fee, 19.0 if responsible else 0.0)


def _costs_section(
    saved: dict[str, Any], currency: str, market: MarketData | None
) -> tuple[OperatingCosts, float]:
    with st.container(border=True):
        st.subheader("3 · Your monthly costs")
        st.caption(
            "Fixed costs you pay every month, and costs you pay every time a guest checks out. "
            "Edit the amounts (add or remove rows as needed)."
        )
        left, right = st.columns(2)
        with left:
            st.markdown("**Fixed, per month**")
            fixed = _cost_editor(saved.get("fixed_monthly") or DEFAULT_FIXED, "fixed", currency)
        with right:
            st.markdown("**Per booking (each check-out)**")
            per_booking = _cost_editor(
                saved.get("per_booking") or DEFAULT_PER_BOOKING, "per_booking", currency
            )
        c1, c2 = st.columns(2)
        market_hint = _market_occupancy(market)
        occupancy = c1.slider(
            "Expected occupancy %",
            0,
            100,
            int(saved.get("occupancy_pct", 75)),
            help="Share of the month's nights you expect to be booked."
            + (
                f" Competitors: about {market_hint:.0f}% of their next 60 days are not "
                "bookable (a rough upper bound)."
                if market_hint is not None
                else ""
            ),
        )
        income_tax = c2.number_input(
            "Income tax provision % (on profit)",
            0.0,
            50.0,
            float(saved.get("income_tax_pct", 0.0)),
            step=1.0,
            help="Optional: set aside part of the profit for income tax.",
        )
    return OperatingCosts(fixed, per_booking, income_tax), float(occupancy)


def _cost_editor(defaults: Mapping[str, object], key: str, currency: str) -> dict[str, float]:
    frame = pd.DataFrame(
        {"Concept": list(defaults), "Amount": [float(str(v)) for v in defaults.values()]}
    )
    edited = st.data_editor(
        frame,
        key=f"_calc_costs_{key}",
        num_rows="dynamic",
        hide_index=True,
        width="stretch",
        column_config={
            "Concept": st.column_config.TextColumn(required=True),
            "Amount": st.column_config.NumberColumn(
                f"Amount ({currency})", min_value=0, step=1000, format="localized"
            ),
        },
    )
    return {
        str(row["Concept"]): float(row["Amount"] or 0)
        for _, row in edited.iterrows()
        if row["Concept"]
    }


# --- results -------------------------------------------------------------------------


def _booking_result(booking: BookingBreakdown, currency: str) -> None:
    section(
        "2 · What one booking leaves you",
        f"A {booking.nights}-night stay, from what the guest pays to what reaches your account.",
    )
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Guest pays", format_money(booking.guest_pays, currency))
    c2.metric(
        "Airbnb keeps",
        format_money(booking.airbnb_fee + booking.vat_on_fee, currency),
        help="Service fee plus the VAT on it.",
    )
    c3.metric(
        "You receive",
        format_money(booking.payout_after_vat, currency),
        help="After Airbnb's fee and, if it applies, the VAT you owe on the stay.",
    )
    c4.metric("Net per night", format_money(booking.per_night, currency))

    lines = [
        (
            f"{booking.nights} nights × {format_money(booking.nightly_after_discount, currency)}"
            + (f" (after {booking.discount_pct:g}% discount)" if booking.discount_pct else ""),
            booking.accommodation,
        ),
        ("Cleaning fee", booking.cleaning_fee),
        ("Guest pays", booking.guest_pays),
        ("Airbnb service fee", -booking.airbnb_fee),
        ("VAT on Airbnb's fee", -booking.vat_on_fee),
        ("Airbnb payout", booking.payout),
        ("VAT you owe on the stay", -booking.stay_vat),
        ("You keep", booking.payout_after_vat),
    ]
    table = pd.DataFrame(
        [(label, round(amount)) for label, amount in lines if amount or "Guest" in label],
        columns=["Line", "Amount"],
    )
    st.dataframe(
        table,
        hide_index=True,
        width="stretch",
        column_config={"Amount": st.column_config.NumberColumn(format="localized")},
    )
    insight(
        f"Of every **{format_money(booking.guest_pays, currency)}** the guest pays, "
        f"**{booking.effective_fee_pct:.1f}%** goes to fees and taxes: you keep "
        f"**{format_money(booking.payout_after_vat, currency)}** "
        f"({format_money(booking.per_night, currency)} per night)."
    )


def _month_result(
    month: MonthlyProjection,
    *,
    pricing: StayPricing,
    policy: FeePolicy,
    costs: OperatingCosts,
    currency: str,
    market: MarketData | None,
) -> None:
    section(
        "4 · A typical month",
        f"30 days at {month.occupancy_pct:.0f}% occupancy: {month.occupied_nights:.0f} nights "
        f"booked, about {month.bookings:.1f} stays of {pricing.nights} nights.",
    )
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Guests pay", format_money(month.guest_payments, currency))
    c2.metric(
        "Fees & taxes",
        format_money(month.airbnb_fees + month.stay_vat + month.income_tax, currency),
    )
    c3.metric("Your costs", format_money(month.booking_costs + month.fixed_costs, currency))
    c4.metric(
        "Net profit",
        format_money(month.net_profit, currency),
        delta=f"{month.margin_pct:.0f}% margin",
        delta_color="off",
    )
    st.altair_chart(_waterfall(month, currency), width="stretch")

    break_even = break_even_nightly_price(pricing, policy, costs, month.occupancy_pct)
    parts = []
    if costs.fixed_total + costs.per_booking_total == 0:
        parts.append(
            "Add your monthly and per-booking costs above to see your real profit and "
            "the minimum price that covers them."
        )
    elif break_even is not None:
        parts.append(
            f"At {month.occupancy_pct:.0f}% occupancy you need at least "
            f"**{format_money(break_even, currency)}** per night to cover every cost."
        )
    typical = _market_typical_price(market, pricing.nights)
    if typical:
        diff = (pricing.nightly_after_discount / typical - 1) * 100
        parts.append(
            f"Your price after discount is **{diff:+.0f}%** versus the typical competitor "
            f"for {pricing.nights}-night stays ({format_money(typical, currency)})."
        )
    if parts:
        insight(" ".join(parts))
    how_to_read(
        "- **Guests pay**: nights after discount plus the cleaning fee, for every stay.\n"
        "- **Fees & taxes**: Airbnb's fee and its VAT, VAT on the stay if you charge it, and "
        "the income tax provision.\n"
        "- **Costs**: your fixed monthly costs plus per-booking costs × number of stays.\n"
        "- Longer stays mean fewer check-outs, so lower per-booking costs."
    )


def _waterfall(month: MonthlyProjection, currency: str) -> alt.Chart:
    steps = [
        ("Guests pay", month.guest_payments),
        ("Airbnb fees", -month.airbnb_fees),
        ("VAT on stay", -month.stay_vat),
        ("Per-booking costs", -month.booking_costs),
        ("Fixed costs", -month.fixed_costs),
        ("Income tax", -month.income_tax),
    ]
    rows, running = [], 0.0
    for label, amount in steps:
        if not amount and label != "Guests pay":
            continue
        start, running = running, running + amount
        rows.append(
            {
                "Step": label,
                "start": start,
                "end": running,
                "amount": amount,
                "kind": "in" if amount > 0 else "out",
            }
        )
    rows.append(
        {"Step": "Net profit", "start": 0.0, "end": running, "amount": running, "kind": "net"}
    )
    frame = pd.DataFrame(rows)
    order = list(frame["Step"])
    bars = (
        alt.Chart(frame)
        .mark_bar(cornerRadius=3)
        .encode(
            x=alt.X("Step:N", sort=order, title=None),
            y=alt.Y("start:Q", title=currency),
            y2="end:Q",
            color=alt.Color(
                "kind:N",
                legend=None,
                scale=alt.Scale(domain=["in", "out", "net"], range=["#B0B0B0", CORAL, INK]),
            ),
            tooltip=["Step", alt.Tooltip("amount:Q", format=",.0f")],
        )
    )
    chart: alt.Chart = bars.properties(height=300)
    return chart


def _airbnb_setup(pricing: StayPricing, currency: str) -> None:
    with st.container(border=True):
        st.subheader("Set it up in Airbnb")
        st.caption("Airbnb → Listings → your listing → Calendar → Pricing / Availability.")
        rows = pd.DataFrame(
            [
                ("Nightly price", format_money(pricing.nightly_price, currency)),
                (
                    f"Weekly discount ({WEEKLY_FROM_NIGHTS}+ nights)",
                    f"{pricing.weekly_discount_pct:g}%",
                ),
                (
                    f"Monthly discount ({MONTHLY_FROM_NIGHTS}+ nights)",
                    f"{pricing.monthly_discount_pct:g}%",
                ),
                ("Cleaning fee", format_money(pricing.cleaning_fee, currency)),
            ],
            columns=["Setting", "Value"],
        )
        st.dataframe(rows, hide_index=True, width="stretch")
        caveat(
            "Airbnb adds its own monthly stay savings for 28+ nights; Airbnb pays for them, "
            "so they do not reduce your payout."
        )


def _save_defaults(
    pricing: StayPricing,
    policy: FeePolicy,
    costs: OperatingCosts,
    occupancy: float,
) -> None:
    if not st.button("Save these values as my defaults", icon=":material/save:"):
        return
    raw = read_raw_config(config_path())
    raw[_RAW_SECTION] = {
        "nightly_price": round(pricing.nightly_price),
        "nights": pricing.nights,
        "cleaning_fee": round(pricing.cleaning_fee),
        "weekly_discount_pct": pricing.weekly_discount_pct,
        "monthly_discount_pct": pricing.monthly_discount_pct,
        "host_fee_pct": policy.host_fee_pct,
        "vat_on_fee_pct": policy.vat_on_fee_pct,
        "stay_vat_pct": policy.stay_vat_pct,
        "fixed_monthly": {k: round(v) for k, v in costs.fixed_monthly.items()},
        "per_booking": {k: round(v) for k, v in costs.per_booking.items()},
        "income_tax_pct": costs.income_tax_pct,
        "occupancy_pct": occupancy,
    }
    save_raw_config(raw, config_path())
    st.toast("Saved: the calculator will open with these values.", icon=":material/check_circle:")


# --- helpers -------------------------------------------------------------------------


def _saved_inputs() -> dict[str, Any]:
    try:
        section_data = read_raw_config(config_path()).get(_RAW_SECTION)
    except Exception:
        return {}
    return dict(section_data) if isinstance(section_data, dict) else {}


def _my_price(settings: AppSettings) -> int | None:
    shared = st.session_state.get("_my_nightly_price")
    configured = settings.my_listing.nightly_price
    return shared or (round(configured) if configured else None)


def _market_discounts(market: MarketData | None) -> tuple[float | None, float | None]:
    if market is None:
        return None, None
    weekly = [
        float(d.median_discount_pct)
        for d in market.bundle.durations
        if WEEKLY_FROM_NIGHTS <= d.nights < MONTHLY_FROM_NIGHTS and d.median_discount_pct
    ]
    monthly = [
        float(d.median_discount_pct)
        for d in market.bundle.durations
        if d.nights >= MONTHLY_FROM_NIGHTS and d.median_discount_pct
    ]
    return (
        round(sum(weekly) / len(weekly)) if weekly else None,
        round(sum(monthly) / len(monthly)) if monthly else None,
    )


def _market_occupancy(market: MarketData | None) -> float | None:
    if market is None:
        return None
    return market.bundle.availability.median_unavailable_pct


def _market_typical_price(market: MarketData | None, nights: int) -> float | None:
    if market is None or not market.bundle.durations:
        return None
    closest = min(market.bundle.durations, key=lambda d: abs(d.nights - nights))
    return float(closest.distribution.median.amount) if closest.distribution else None


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value:g}%"
