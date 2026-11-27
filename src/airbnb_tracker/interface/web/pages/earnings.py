"""Earnings calculator: how to configure Airbnb, what each booking leaves and the month's P&L."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import altair as alt
import pandas as pd
import streamlit as st

from airbnb_tracker.domain.earnings import (
    DAYS_PER_MONTH,
    MONTHLY_FROM_NIGHTS,
    WEEKLY_FROM_NIGHTS,
    BookingBreakdown,
    FeePolicy,
    MonthlyProjection,
    OperatingCosts,
    StayPricing,
    base_for_effective,
    break_even_nightly_price,
    nightly_price_for_payout,
    project_month,
    quote_booking,
)
from airbnb_tracker.domain.exceptions import InvalidValueError
from airbnb_tracker.infrastructure.config import AppSettings, read_raw_config, save_raw_config
from airbnb_tracker.interface.web import finance_settings
from airbnb_tracker.interface.web.components import caveat, how_to_read, insight, section
from airbnb_tracker.interface.web.context import config_path
from airbnb_tracker.interface.web.data import MarketData, load_market
from airbnb_tracker.interface.web.formatting import forget_money_input, format_money, money_input
from airbnb_tracker.interface.web.glossary import (
    BASE_RATE,
    GUEST_TOTAL,
    HOST_PAYOUT,
    glossary,
)
from airbnb_tracker.interface.web.guards import require_settings
from airbnb_tracker.interface.web.i18n import t
from airbnb_tracker.interface.web.navigation import link
from airbnb_tracker.interface.web.theme import CORAL, INK

CALCULATOR_PRICE = "_calc_nightly_price"
CALCULATOR_NIGHTS = "_calc_nights"
# Prices handed over from the advisor are *after discount*: converted to a base rate here.
PENDING_NIGHTLY_AFTER_DISCOUNT = "_calc_pending_after_discount"
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
    "[Airbnb]({fees_url}), [Airbnb Q4 2025 results]({results_url}).\n"
    "- **19% VAT on Airbnb's fee in Colombia**: [Renteo]({renteo_url}).\n"
    "- **19% VAT on the stay** only if you are *responsable de IVA*: "
    "[Airbnb tax guide Colombia 2026]({tax_url}).\n"
    "- **Discounts** apply to nights only (weekly from 7, monthly from 28 nights), never to "
    "the cleaning fee: [Airbnb help]({discounts_url})."
)


def render() -> None:
    st.title(t("Earnings calculator"))
    st.caption(t("Pricing"))
    st.markdown(
        t(
            "Set a price and see **what you really earn**: Airbnb's fees, taxes and your costs. "
            "It also tells you **what to enter in Airbnb** (price, discounts, cleaning fee)."
        )
    )
    settings = require_settings()
    if settings is None:
        return
    glossary()
    currency = settings.scraping.currency
    saved = finance_settings.saved_inputs()
    market = load_market(settings, None)

    policy = _fees_section(saved)
    pricing = _pricing_section(settings, saved, market, policy)
    if pricing is None:
        return
    booking = quote_booking(pricing, policy)
    _booking_result(booking, currency)

    costs, paid_nights = _costs_section(saved, currency)
    occupancy = paid_nights / DAYS_PER_MONTH * 100
    month = project_month(pricing, policy, costs, occupancy)
    _month_result(
        month, pricing=pricing, policy=policy, costs=costs, currency=currency, market=market
    )
    _airbnb_setup(pricing, currency)
    _save_defaults(pricing, policy, costs, paid_nights)


# --- inputs --------------------------------------------------------------------------


def _pricing_section(
    settings: AppSettings, saved: dict[str, Any], market: MarketData | None, policy: FeePolicy
) -> StayPricing | None:
    currency = settings.scraping.currency
    mine = settings.my_listing
    weekly_typical, monthly_typical = _market_discounts(market)
    with st.container(border=True):
        st.subheader(t("1 · Your Airbnb pricing"))
        st.caption(t("The values you set in Airbnb → Calendar → Pricing."))
        reverse = st.toggle(
            t("Start from what I want to receive per night"),
            help=t("Enter your target net income per night and get the price to publish."),
        )
        c1, c2, c3, c4 = st.columns(4)
        st.session_state.setdefault(CALCULATOR_NIGHTS, int(saved.get("nights", 30)))
        nights = c1.selectbox(
            t("Stay length to simulate"),
            _STAY_CHOICES,
            key=CALCULATOR_NIGHTS,
            format_func=lambda n: t("{nights} nights", nights=n) if n > 1 else t("1 night"),
        )
        with c2:
            cleaning = money_input(
                t("Cleaning fee per stay"),
                key="_calc_cleaning",
                currency=currency,
                default=round(mine.cleaning_fee) if mine.cleaning_fee else None,
            )
        weekly = c3.number_input(
            t("Weekly discount %"),
            0,
            99,
            round(mine.weekly_discount_pct or weekly_typical or 0),
            help=t(
                "From {nights} nights. Market typical: {pct}.",
                nights=WEEKLY_FROM_NIGHTS,
                pct=_pct(weekly_typical),
            ),
        )
        monthly = c4.number_input(
            t("Monthly discount %"),
            0,
            99,
            round(mine.monthly_discount_pct or monthly_typical or 0),
            help=t(
                "From {nights} nights. Market typical: {pct}.",
                nights=MONTHLY_FROM_NIGHTS,
                pct=_pct(monthly_typical),
            ),
        )
        if weekly_typical or monthly_typical:
            st.caption(
                t(
                    "Market typical discounts: weekly {weekly}, monthly {monthly} (median of "
                    "competitors that discount).",
                    weekly=_pct(weekly_typical),
                    monthly=_pct(monthly_typical),
                )
            )

        template = StayPricing(1.0, int(nights), weekly, monthly, float(cleaning or 0))
        if reverse:
            target = money_input(
                t("I want to receive per night"),
                key="_calc_target",
                currency=currency,
                default=saved.get("target_per_night"),
            )
            if not target:
                st.info(t("Enter how much you want to receive per night."))
                return None
            try:
                price = nightly_price_for_payout(float(target), template, policy)
            except InvalidValueError as error:
                st.error(str(error))
                return None
            insight(
                t(
                    "Publish **{price}** per night to receive **{target}** per night on "
                    "{nights}-night stays, after discounts, Airbnb's fee and taxes.",
                    price=format_money(price, currency),
                    target=format_money(target, currency),
                    nights=nights,
                )
            )
        else:
            handed_over = st.session_state.pop(PENDING_NIGHTLY_AFTER_DISCOUNT, None)
            if handed_over is not None:
                base = base_for_effective(float(handed_over), template.discount_pct)
                st.session_state[CALCULATOR_PRICE] = round(base)
                forget_money_input(CALCULATOR_PRICE)
                st.caption(
                    t(
                        "From the advisor: {price} nightly after discount → base nightly rate "
                        "{base} with your {discount:g}% discount.",
                        price=format_money(handed_over, currency),
                        base=format_money(base, currency),
                        discount=template.discount_pct,
                    )
                )
            default_price = round(mine.nightly_price) if mine.nightly_price else None
            price_input = money_input(
                t(BASE_RATE),
                key=CALCULATOR_PRICE,
                currency=currency,
                default=default_price,
            )
            if not price_input:
                st.info(t("Enter your base nightly rate: the price you publish, before discounts."))
                return None
            price = float(price_input)
    return StayPricing(price, int(nights), weekly, monthly, float(cleaning or 0))


def _fees_section(saved: dict[str, Any]) -> FeePolicy:
    """Airbnb's fee and taxes, as set in Settings → Listing configuration (read only here)."""
    policy = finance_settings.fee_policy(saved)
    with st.expander(
        t(
            "Airbnb fees and taxes: {fee:g}% fee + {vat:g}% VAT on it",
            fee=policy.host_fee_pct,
            vat=policy.vat_on_fee_pct,
        ),
        icon=":material/percent:",
    ):
        if policy.stay_vat_pct:
            st.markdown(t("You charge VAT on the stay ({vat:g}%).", vat=policy.stay_vat_pct))
        st.markdown(
            t(
                _FEE_SOURCES,
                fees_url=_AIRBNB_FEES_URL,
                results_url=_AIRBNB_RESULTS_URL,
                renteo_url=_RENTEO_URL,
                tax_url=_TAX_GUIDE_URL,
                discounts_url=_DISCOUNTS_URL,
            )
        )
        caveat(
            t("Rates change and depend on your tax situation: confirm them with your accountant.")
        )
        link("settings", t("Change them in Settings"), ":material/settings:")
    return policy


def _costs_section(saved: dict[str, Any], currency: str) -> tuple[OperatingCosts, int]:
    with st.container(border=True):
        st.subheader(t("3 · Your monthly costs"))
        st.caption(
            t(
                "Fixed costs you pay every month, and costs you pay every time a guest checks "
                "out. Edit the amounts (add or remove rows as needed)."
            )
        )
        left, right = st.columns(2)
        with left:
            st.markdown(t("**Fixed, per month**"))
            fixed = _cost_editor(
                saved.get("fixed_monthly") or _translated(DEFAULT_FIXED), "fixed", currency
            )
        with right:
            st.markdown(t("**Per booking (each check-out)**"))
            per_booking = _cost_editor(
                saved.get("per_booking") or _translated(DEFAULT_PER_BOOKING),
                "per_booking",
                currency,
            )
        c1, c2 = st.columns(2)
        paid_nights = c1.number_input(
            t("Paid nights per 30-day month"),
            1,
            30,
            finance_settings.paid_nights(saved),
            help=t(
                "Nights actually paid out of the 30 committed: long stays leave gaps between "
                "guests."
            ),
        )
        income_tax = c2.number_input(
            t("Income tax provision % (on profit)"),
            0.0,
            50.0,
            float(saved.get("income_tax_pct", 0.0)),
            step=1.0,
            help=t("Optional: set aside part of the profit for income tax."),
        )
    return OperatingCosts(fixed, per_booking, income_tax), int(paid_nights)


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
            "Concept": st.column_config.TextColumn(t("Concept"), required=True),
            "Amount": st.column_config.NumberColumn(
                t("Amount ({currency})", currency=currency),
                min_value=0,
                step=1000,
                format="localized",
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
        t("2 · What one booking leaves you"),
        t(
            "A {nights}-night stay, from what the guest pays to what reaches your account.",
            nights=booking.nights,
        ),
    )
    c1, c2, c3, c4 = st.columns(4)
    c1.metric(t(GUEST_TOTAL), format_money(booking.guest_pays, currency))
    c2.metric(
        t("Airbnb fees"),
        format_money(booking.airbnb_fee + booking.vat_on_fee, currency),
        help=t("Service fee plus the VAT on it."),
    )
    c3.metric(
        t("You receive"),
        format_money(booking.payout_after_vat, currency),
        help=t("After Airbnb's fee and, if it applies, the VAT you owe on the stay."),
    )
    c4.metric(
        t("{payout} per night", payout=t(HOST_PAYOUT)), format_money(booking.per_night, currency)
    )

    nightly = format_money(booking.nightly_after_discount, currency)
    stay_line = (
        t(
            "{nights} nights × {price} (after {discount:g}% discount)",
            nights=booking.nights,
            price=nightly,
            discount=booking.discount_pct,
        )
        if booking.discount_pct
        else t("{nights} nights × {price}", nights=booking.nights, price=nightly)
    )
    lines = [
        (stay_line, booking.accommodation),
        (t("Cleaning fee"), booking.cleaning_fee),
        (t(GUEST_TOTAL), booking.guest_pays),
        (t("Airbnb service fee"), -booking.airbnb_fee),
        (t("VAT on Airbnb's fee"), -booking.vat_on_fee),
        (t(HOST_PAYOUT), booking.payout),
        (t("VAT you owe on the stay"), -booking.stay_vat),
        (t("You keep after VAT"), booking.payout_after_vat),
    ]
    amount_column = t("Amount")
    table = pd.DataFrame(
        [(label, round(amount)) for label, amount in lines if amount or label == t(GUEST_TOTAL)],
        columns=[t("Line"), amount_column],
    )
    st.dataframe(
        table,
        hide_index=True,
        width="stretch",
        column_config={amount_column: st.column_config.NumberColumn(format="localized")},
    )
    insight(
        t(
            "Of every **{guest}** the guest pays, **{fees:.1f}%** goes to fees and taxes: you "
            "keep **{payout}** ({per_night} per night).",
            guest=format_money(booking.guest_pays, currency),
            fees=booking.effective_fee_pct,
            payout=format_money(booking.payout_after_vat, currency),
            per_night=format_money(booking.per_night, currency),
        )
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
        t("4 · A typical month"),
        t(
            "30 days at {occupancy:.0f}% occupancy: {booked:.0f} nights booked, about "
            "{stays:.1f} stays of {nights} nights.",
            occupancy=month.occupancy_pct,
            booked=month.occupied_nights,
            stays=month.bookings,
            nights=pricing.nights,
        ),
    )
    c1, c2, c3, c4 = st.columns(4)
    c1.metric(t("Guests pay"), format_money(month.guest_payments, currency))
    c2.metric(
        t("Fees & taxes"),
        format_money(month.airbnb_fees + month.stay_vat + month.income_tax, currency),
    )
    c3.metric(t("Your costs"), format_money(month.booking_costs + month.fixed_costs, currency))
    c4.metric(
        t("Net profit"),
        format_money(month.net_profit, currency),
        delta=t("{margin:.0f}% margin", margin=month.margin_pct),
        delta_color="off",
    )
    st.altair_chart(_waterfall(month, currency), width="stretch")

    break_even = break_even_nightly_price(pricing, policy, costs, month.occupancy_pct)
    parts = []
    if costs.fixed_total + costs.per_booking_total == 0:
        parts.append(
            t(
                "Add your monthly and per-booking costs above to see your real profit and "
                "the minimum price that covers them."
            )
        )
    elif break_even is not None:
        parts.append(
            t(
                "At {occupancy:.0f}% occupancy you need at least **{price}** per night to "
                "cover every cost.",
                occupancy=month.occupancy_pct,
                price=format_money(break_even, currency),
            )
        )
    typical = _market_typical_price(market, pricing.nights)
    if typical:
        diff = (pricing.nightly_after_discount / typical - 1) * 100
        parts.append(
            t(
                "Your price after discount is **{diff:+.0f}%** versus the typical competitor "
                "for {nights}-night stays ({typical}).",
                diff=diff,
                nights=pricing.nights,
                typical=format_money(typical, currency),
            )
        )
    if parts:
        insight(" ".join(parts))
    how_to_read(
        t(
            "- **Guests pay**: nights after discount plus the cleaning fee, for every stay.\n"
            "- **Fees & taxes**: Airbnb's fee and its VAT, VAT on the stay if you charge it, "
            "and the income tax provision.\n"
            "- **Costs**: your fixed monthly costs plus per-booking costs × number of stays.\n"
            "- Longer stays mean fewer check-outs, so lower per-booking costs."
        )
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
                "Step": t(label),
                "start": start,
                "end": running,
                "amount": amount,
                "kind": "in" if amount > 0 else "out",
            }
        )
    rows.append(
        {"Step": t("Net profit"), "start": 0.0, "end": running, "amount": running, "kind": "net"}
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
            tooltip=[
                alt.Tooltip("Step:N", title=t("Step")),
                alt.Tooltip("amount:Q", title=t("Amount"), format=",.0f"),
            ],
        )
    )
    chart: alt.Chart = bars.properties(height=300)
    return chart


def _airbnb_setup(pricing: StayPricing, currency: str) -> None:
    with st.container(border=True):
        st.subheader(t("Set it up in Airbnb"))
        st.caption(t("Airbnb → Listings → your listing → Calendar → Pricing / Availability."))
        rows = pd.DataFrame(
            [
                (t(BASE_RATE), format_money(pricing.nightly_price, currency)),
                (
                    t("Weekly discount ({nights}+ nights)", nights=WEEKLY_FROM_NIGHTS),
                    f"{pricing.weekly_discount_pct:g}%",
                ),
                (
                    t("Monthly discount ({nights}+ nights)", nights=MONTHLY_FROM_NIGHTS),
                    f"{pricing.monthly_discount_pct:g}%",
                ),
                (t("Cleaning fee"), format_money(pricing.cleaning_fee, currency)),
            ],
            columns=[t("Setting"), t("Value")],
        )
        st.dataframe(rows, hide_index=True, width="stretch")
        caveat(
            t(
                "Airbnb adds its own monthly stay savings for 28+ nights; Airbnb pays for "
                "them, so they do not reduce your payout."
            )
        )


def _save_defaults(
    pricing: StayPricing,
    policy: FeePolicy,
    costs: OperatingCosts,
    paid_nights: int,
) -> None:
    if not st.button(t("Save these values as my defaults"), icon=":material/save:"):
        return
    raw = read_raw_config(config_path())
    raw.setdefault("my_listing", {}).update(
        {
            "nightly_price": round(pricing.nightly_price),
            "cleaning_fee": round(pricing.cleaning_fee),
            "weekly_discount_pct": pricing.weekly_discount_pct,
            "monthly_discount_pct": pricing.monthly_discount_pct,
        }
    )
    raw[finance_settings.SECTION] = {
        "nights": pricing.nights,
        "host_fee_pct": policy.host_fee_pct,
        "vat_on_fee_pct": policy.vat_on_fee_pct,
        "stay_vat_pct": policy.stay_vat_pct,
        "fixed_monthly": {k: round(v) for k, v in costs.fixed_monthly.items()},
        "per_booking": {k: round(v) for k, v in costs.per_booking.items()},
        "income_tax_pct": costs.income_tax_pct,
        "paid_nights_per_month": paid_nights,
    }
    save_raw_config(raw, config_path())
    st.toast(
        t("Saved: the calculator will open with these values."), icon=":material/check_circle:"
    )


# --- helpers -------------------------------------------------------------------------


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


def _market_typical_price(market: MarketData | None, nights: int) -> float | None:
    if market is None or not market.bundle.durations:
        return None
    closest = min(market.bundle.durations, key=lambda d: abs(d.nights - nights))
    return float(closest.distribution.median.amount) if closest.distribution else None


def _translated(defaults: Mapping[str, int]) -> dict[str, int]:
    """Default cost names in the current language (saved values keep what was typed)."""
    return {t(name): amount for name, amount in defaults.items()}


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value:g}%"
