"""Price & configure: the admin's main screen.

Pick a check-in date, compare your nightly price after discount with the comparables,
try a base rate or a discount, and get the values to enter in Airbnb, in one place.
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd
import streamlit as st

from airbnb_tracker.application.pricing_simulator import (
    CurrentSetup,
    Keep,
    Scenario,
    SimulationInputs,
    aligned_scenario,
    current_scenario,
    discount_kind,
    margin_scenario,
)
from airbnb_tracker.infrastructure.config import AppSettings, read_raw_config, save_raw_config
from airbnb_tracker.interface.web import analytics, finance_settings
from airbnb_tracker.interface.web.components import caveat, insight, nights_picker, section
from airbnb_tracker.interface.web.context import config_path
from airbnb_tracker.interface.web.data import MarketData
from airbnb_tracker.interface.web.formatting import format_money
from airbnb_tracker.interface.web.glossary import (
    ACCOMMODATION,
    BASE_RATE,
    GUEST_TOTAL,
    HOST_PAYOUT,
    NIGHTLY_AFTER_DISCOUNT,
    glossary,
)
from airbnb_tracker.interface.web.guards import require_data, require_settings
from airbnb_tracker.interface.web.navigation import link

_NO_DATA = "No data"


def render() -> None:
    st.title("Price & configure")
    st.caption("Pricing")
    st.markdown(
        "Choose a check-in date, compare your price with the comparables, try a base rate "
        "or a discount and get the exact values to enter in Airbnb."
    )
    settings = require_settings()
    if settings is None:
        return
    data = require_data(settings)
    if data is None:
        return
    mine = settings.my_listing
    if not mine.nightly_price:
        st.warning(
            "Enter your base nightly rate, discounts and cleaning fee in *Set up my market* "
            "to compare and simulate.",
            icon=":material/edit:",
        )
        link("setup", "Set up my market", ":material/tune:")
        return
    glossary()

    saved = finance_settings.saved_inputs()
    pricing = data.tables["pricing_snapshots"]
    c1, c2, c3 = st.columns([2, 1, 1])
    with c1:
        nights = nights_picker(pricing["nights"].unique(), key="setup_nights")
    paid = c2.number_input(
        "Paid nights per 30-day month", 1, 30, finance_settings.paid_nights(saved),
        help="Long stays leave gaps between guests: nights paid out of the 30 days committed.",
    )  # fmt: skip
    target_margin = c3.number_input(
        "Target margin %", 1, 90, 30, help="Net profit ÷ guest payments."
    )

    comp_ids, comp_note = _comparable_ids(data, settings, nights)
    current = CurrentSetup(
        base_nightly=float(mine.nightly_price),
        weekly_discount_pct=mine.weekly_discount_pct,
        monthly_discount_pct=mine.monthly_discount_pct,
        cleaning_fee=float(mine.cleaning_fee or 0),
    )
    yours = current.base_nightly * (1 - mine.discount_for(nights) / 100)
    table = analytics.benchmarks_by_check_in(
        pricing, comp_ids, nights,
        analytics.searched_check_ins(data.raw_tables["search_runs"], nights),
    )  # fmt: skip
    row = _date_table(table, yours, nights, settings.scraping.currency, comp_note)
    if row is None:
        return

    inputs = SimulationInputs(
        current=current,
        nights=nights,
        paid_nights=float(paid),
        policy=finance_settings.fee_policy(saved),
        costs=finance_settings.operating_costs(saved),
        keep=_keep_choice(),
    )
    scenarios = _simulate(inputs, row, float(target_margin), settings.scraping.currency)
    if scenarios:
        _setup_sheet(settings, scenarios, row, nights)


# --- the date table -----------------------------------------------------------------


def _comparable_ids(data: MarketData, settings: AppSettings, nights: int) -> tuple[set[str], str]:
    with st.expander("Which listings count as comparables"):
        listings = data.listings
        mine = analytics.my_listing(listings)
        radius = max(int(settings.search.radius_km), 1)
        c1, c2, c3 = st.columns(3)
        max_km = c1.slider("Max distance (km)", 1, radius, radius, key="setup_km")
        same_bedrooms = c2.toggle("Same bedrooms as mine", value=mine is not None,
                                  disabled=mine is None, key="setup_bedrooms")  # fmt: skip
        same_baths = c3.toggle("At least my bathrooms", value=False,
                               disabled=mine is None, key="setup_baths")  # fmt: skip
    nightly = analytics.nightly_by_listing(data.tables["pricing_snapshots"], nights)
    baths = analytics.as_float(mine["bathrooms"]) if mine is not None and same_baths else None
    comps = analytics.comparables(
        listings, nightly, max_km=max_km, same_bedrooms=same_bedrooms, min_bathrooms=baths
    )
    parts = [f"≤ {max_km} km"]
    if same_bedrooms and mine is not None:
        parts.append(f"{analytics.as_float(mine['bedrooms']) or 0:g} bedrooms")
    if baths:
        parts.append(f"≥ {baths:g} bathrooms")
    return set(comps["listing_id"]), ", ".join(parts)


def _date_table(
    table: pd.DataFrame, yours: float, nights: int, currency: str, comp_note: str
) -> pd.Series | None:
    section(
        f"{nights}-night stays by check-in date",
        f"Comparables ({comp_note}), latest search. Select a row to simulate it.",
    )
    view = pd.DataFrame(
        {
            "Check-in": table["check_in"],
            f"Your {NIGHTLY_AFTER_DISCOUNT.lower()}": round(yours),
            "Comparables median": table["median"].round(0),
            "Range P25–P75": [
                f"{format_money(lo, currency)} – {format_money(hi, currency)}"
                if pd.notna(lo) else f"{_NO_DATA} for {nights} nights"
                for lo, hi in zip(table["p25"], table["p75"], strict=True)
            ],
            "vs median": ((yours / table["median"] - 1) * 100).round(0),
            "Comparables": table["comparables"].fillna(0).astype(int),
            "Captured": [_date(v) for v in table["captured"]],
        }
    )  # fmt: skip
    amount = st.column_config.NumberColumn(format="localized")
    event = st.dataframe(
        view,
        hide_index=True,
        width="stretch",
        on_select="rerun",
        selection_mode="single-row",
        key="setup_dates",
        column_config={
            f"Your {NIGHTLY_AFTER_DISCOUNT.lower()}": amount,
            "Comparables median": amount,
            "vs median": st.column_config.NumberColumn(format="%+.0f%%"),
        },
    )
    with_data = table.index[table["median"].notna()].tolist()
    if not with_data:
        st.info(f"{_NO_DATA} for {nights} nights: collect prices for this stay length first.")
        return None
    selected = event.selection.rows[0] if event.selection.rows else with_data[0]
    row = table.loc[selected]
    if pd.isna(row["median"]):
        st.info(f"{_NO_DATA} for {nights} nights on {row['check_in']}: pick another date.")
        return None
    st.caption(
        f"Simulating check-in **{row['check_in']}** ({int(row['comparables'])} comparables)."
    )
    return row


def _keep_choice() -> Keep:
    labels = {
        Keep.BASE.value: "my base rate (change the discount)",
        Keep.DISCOUNT.value: "my discount (change the base rate)",
    }
    choice = st.radio(
        "When adjusting, keep", list(labels), format_func=labels.__getitem__, horizontal=True
    )
    return Keep(choice)


# --- simulator ------------------------------------------------------------------------


def _simulate(
    inputs: SimulationInputs, row: pd.Series, target_margin: float, currency: str
) -> list[Scenario]:
    median = float(row["median"])
    scenarios = [current_scenario(inputs), aligned_scenario(inputs, median, "Aligned with median")]
    margin = margin_scenario(inputs, target_margin)
    if margin is not None:
        scenarios.append(margin)
    section(
        "Simulation",
        f"{inputs.nights}-night stay, {inputs.paid_nights:g} paid nights per 30-day month, "
        "with the fees and costs of the Earnings calculator.",
    )
    kind = discount_kind(inputs.nights)
    discount_label = f"{kind.capitalize()} discount" if kind else "Discount"
    accommodation_label = f"{ACCOMMODATION} ({inputs.nights} nights)"
    rows: dict[str, list[str]] = {}
    for s in scenarios:
        e = s.economics
        values = {
            BASE_RATE: format_money(s.pricing.nightly_price, currency),
            discount_label: f"{s.pricing.discount_pct:.1f}%",
            NIGHTLY_AFTER_DISCOUNT: format_money(s.nightly_after_discount, currency),
            "vs comparables median": f"{(s.nightly_after_discount / median - 1) * 100:+.0f}%",
            accommodation_label: format_money(e.booking.accommodation, currency),
            GUEST_TOTAL: format_money(e.booking.guest_pays, currency),
            HOST_PAYOUT: format_money(e.booking.payout_after_vat, currency),
            "Margin per stay": format_money(e.margin_per_stay, currency),
            "Margin per committed night": format_money(e.margin_per_committed_night, currency),
            "Monthly margin": f"{e.margin_pct:.0f}%",
        }  # fmt: skip
        for label, value in values.items():
            rows.setdefault(label, []).append(value)
    st.dataframe(
        pd.DataFrame(rows, index=[s.name for s in scenarios]).T,
        width="stretch",
    )
    current, aligned = scenarios[0], scenarios[1]
    gap = (current.nightly_after_discount / median - 1) * 100
    insight(
        f"Your {NIGHTLY_AFTER_DISCOUNT.lower()} is **{gap:+.0f}%** versus the comparables "
        f"median. To align: {BASE_RATE.lower()} "
        f"**{format_money(aligned.pricing.nightly_price, currency)}** with "
        f"**{aligned.pricing.discount_pct:.1f}%** {discount_label.lower()}, leaving a "
        f"**{aligned.economics.margin_pct:.0f}%** monthly margin."
    )
    if not finance_settings.operating_costs(finance_settings.saved_inputs()).fixed_monthly:
        caveat("No costs saved yet: margins only deduct Airbnb's fees. Add your costs in the "
               "Earnings calculator.")  # fmt: skip
    return scenarios


# --- what to enter in Airbnb -------------------------------------------------------------


def _setup_sheet(
    settings: AppSettings, scenarios: list[Scenario], row: pd.Series, nights: int
) -> None:
    currency = settings.scraping.currency
    mine = settings.my_listing
    section("Set it up in Airbnb", "Current → proposed, ready to apply.")
    options = {s.name: s for s in scenarios[1:]}
    choice = st.radio("Apply", list(options), horizontal=True, key="setup_apply")
    proposed = options[str(choice)]
    min_stay = st.number_input(
        "Proposed minimum stay (nights)", 1, 365, int(mine.min_nights or nights), key="setup_min"
    )
    p = proposed.pricing
    sheet = pd.DataFrame(
        [
            (BASE_RATE, format_money(float(mine.nightly_price or 0), currency),
             format_money(p.nightly_price, currency)),
            ("Weekly discount (7+ nights)", f"{mine.weekly_discount_pct:g}%",
             f"{p.weekly_discount_pct:.1f}%"),
            ("Monthly discount (28+ nights)", f"{mine.monthly_discount_pct:g}%",
             f"{p.monthly_discount_pct:.1f}%"),
            ("Cleaning fee", format_money(float(mine.cleaning_fee or 0), currency),
             format_money(p.cleaning_fee, currency)),
            ("Minimum stay", f"{mine.min_nights or '—'} nights", f"{min_stay} nights"),
        ],
        columns=["Setting", "Current", "Proposed"],
    )  # fmt: skip
    st.dataframe(sheet, hide_index=True, width="stretch")

    booking = proposed.economics.booking
    with st.container(border=True):
        st.markdown(f"**Verify the {nights}-night quote in Airbnb before applying**")
        st.markdown(
            f"In Airbnb, open your calendar, select check-in **{row['check_in']}** for "
            f"{nights} nights and check the price preview. With the proposed settings you "
            "should see:\n"
            f"- {ACCOMMODATION}: **{format_money(booking.accommodation, currency)}**\n"
            f"- {GUEST_TOTAL}: **{format_money(booking.guest_pays, currency)}**\n"
            f"- {HOST_PAYOUT}: **{format_money(booking.payout_after_vat, currency)}**"
        )
        caveat("Small differences are normal: Airbnb rounds and may add its own monthly "
               "savings, which it pays for.")  # fmt: skip
    if st.button("Save the proposal as my current pricing", icon=":material/save:"):
        raw = read_raw_config(config_path())
        listing = raw.setdefault("my_listing", {})
        listing.update(
            {
                "nightly_price": round(p.nightly_price),
                "weekly_discount_pct": round(p.weekly_discount_pct, 1),
                "monthly_discount_pct": round(p.monthly_discount_pct, 1),
                "cleaning_fee": round(p.cleaning_fee),
                "min_nights": int(min_stay),
            }
        )
        save_raw_config(raw, config_path())
        st.toast("Saved. Apply the same values in Airbnb.", icon=":material/check_circle:")


def _date(value: object) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "—"
    return datetime.fromisoformat(str(value)).strftime("%Y-%m-%d")
