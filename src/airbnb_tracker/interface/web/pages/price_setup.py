"""Price & configure: the admin's main screen.

Pick a check-in date, compare your nightly price after discount with the comparables,
try a base rate or a discount, and get the values to enter in Airbnb, in one place.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

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
from airbnb_tracker.interface.web.formatting import format_date, format_datetime, format_money
from airbnb_tracker.interface.web.glossary import (
    ACCOMMODATION,
    BASE_RATE,
    GUEST_TOTAL,
    HOST_PAYOUT,
    NIGHTLY_AFTER_DISCOUNT,
    glossary,
)
from airbnb_tracker.interface.web.guards import require_data, require_settings
from airbnb_tracker.interface.web.i18n import t
from airbnb_tracker.interface.web.navigation import link
from airbnb_tracker.interface.web.ui_state import remember, remembered


def render() -> None:
    st.title(t("Price & configure"))
    st.caption(t("Pricing"))
    st.markdown(
        t(
            "Choose a check-in date, compare your price with the comparables, try a base rate "
            "or a discount and get the exact values to enter in Airbnb."
        )
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
            t(
                "Enter your base nightly rate, discounts and cleaning fee in *Settings* to "
                "compare and simulate."
            ),
            icon=":material/edit:",
        )
        link("settings", t("Settings"), ":material/settings:")
        return
    glossary()
    last = remembered(settings)

    saved = finance_settings.saved_inputs()
    pricing = data.tables["pricing_snapshots"]
    c1, c2, c3 = st.columns([2, 1, 1])
    with c1:
        nights = nights_picker(
            pricing["nights"].unique(), key="setup_nights", preferred=last.get("nights")
        )
    paid = c2.number_input(
        t("Paid nights per 30-day month"),
        1,
        30,
        finance_settings.paid_nights(saved),
        help=t("Long stays leave gaps between guests: nights paid out of the 30 days committed."),
    )
    target_margin = c3.number_input(
        t("Minimum margin %"),
        1,
        90,
        30,
        help=t("Net profit ÷ guest payments. The simulation shows the lowest price that keeps it."),
    )

    comp_ids, comp_note = _comparable_ids(data, settings, nights, last)
    current = CurrentSetup(
        base_nightly=float(mine.nightly_price),
        weekly_discount_pct=mine.weekly_discount_pct,
        monthly_discount_pct=mine.monthly_discount_pct,
        cleaning_fee=float(mine.cleaning_fee or 0),
    )
    yours = current.base_nightly * (1 - mine.discount_for(nights) / 100)
    runs = data.tables["search_runs"]
    table = analytics.benchmarks_by_check_in(
        pricing, comp_ids, nights, analytics.searched_check_ins(runs, nights)
    ).merge(analytics.latest_attempts(runs, nights), on="check_in", how="left")
    row = _date_table(
        table,
        yours=yours,
        nights=nights,
        currency=settings.scraping.currency,
        comp_note=comp_note,
        last=last,
    )
    if row is None:
        return
    remember(
        settings,
        nights=nights,
        check_in=str(row["check_in"])[:10],
        max_km=st.session_state["setup_km"],
        same_bedrooms=st.session_state["setup_bedrooms"],
        same_baths=st.session_state["setup_baths"],
    )

    inputs = SimulationInputs(
        current=current,
        nights=nights,
        paid_nights=float(paid),
        policy=finance_settings.fee_policy(saved),
        costs=finance_settings.operating_costs(saved),
        keep=_keep_choice(),
    )
    scenarios, names = _simulate(inputs, row, float(target_margin), settings.scraping.currency)
    if scenarios:
        _setup_sheet(settings, scenarios, names, row, nights)


# --- the date table -----------------------------------------------------------------


def _comparable_ids(
    data: MarketData, settings: AppSettings, nights: int, last: dict[str, Any]
) -> tuple[set[str], str]:
    with st.expander(t("Which listings count as comparables")):
        listings = data.listings
        mine = analytics.my_listing(listings)
        radius = max(int(settings.search.radius_km), 1)
        c1, c2, c3 = st.columns(3)
        km = int(last.get("max_km", radius))
        max_km = c1.slider(
            t("Max distance (km)"), 1, radius, min(max(km, 1), radius), key="setup_km"
        )
        same_bedrooms = c2.toggle(
            t("Same bedrooms as mine"),
            value=mine is not None and bool(last.get("same_bedrooms", True)),
            disabled=mine is None,
            key="setup_bedrooms",
        )
        same_baths = c3.toggle(
            t("At least my bathrooms"),
            value=mine is not None and bool(last.get("same_baths", False)),
            disabled=mine is None,
            key="setup_baths",
        )
    nightly = analytics.nightly_by_listing(data.tables["pricing_snapshots"], nights)
    baths = analytics.as_float(mine["bathrooms"]) if mine is not None and same_baths else None
    comps = analytics.comparables(
        listings, nightly, max_km=max_km, same_bedrooms=same_bedrooms, min_bathrooms=baths
    )
    parts = [t("≤ {km} km", km=max_km)]
    if same_bedrooms and mine is not None:
        parts.append(t("{bedrooms:g} bedrooms", bedrooms=analytics.as_float(mine["bedrooms"]) or 0))
    if baths:
        parts.append(t("≥ {baths:g} bathrooms", baths=baths))
    return set(comps["listing_id"]), ", ".join(parts)


def _date_table(
    table: pd.DataFrame,
    *,
    yours: float,
    nights: int,
    currency: str,
    comp_note: str,
    last: dict[str, Any],
) -> pd.Series | None:
    section(
        t("{nights}-night stays by check-in date", nights=nights),
        t("Comparables ({note}), latest search. Select a row to simulate it.", note=comp_note),
    )
    yours_column = t("Your nightly after discount")
    median_column = t("Comparables median")
    gap_column = t("vs median")
    no_data = t("No data for {nights} nights", nights=nights)
    view = pd.DataFrame(
        {
            t("Check-in"): [format_date(v) for v in table["check_in"]],
            yours_column: round(yours),
            median_column: table["median"].round(0),
            t("Range P25–P75"): [
                f"{format_money(lo, currency)} – {format_money(hi, currency)}"
                if pd.notna(lo)
                else no_data
                for lo, hi in zip(table["p25"], table["p75"], strict=True)
            ],
            gap_column: ((yours / table["median"] - 1) * 100).round(0),
            t("Comparables"): table["comparables"].fillna(0).astype(int),
            t("Captured"): [format_date(v) if pd.notna(v) else "—" for v in table["captured"]],
            t("Search"): [_search_note(r) for _, r in table.iterrows()],
        }
    )
    amount = st.column_config.NumberColumn(format="localized")
    event = st.dataframe(
        view,
        hide_index=True,
        width="stretch",
        on_select="rerun",
        selection_mode="single-row",
        key="setup_dates",
        column_config={
            yours_column: amount,
            median_column: amount,
            gap_column: st.column_config.NumberColumn(format="%+.0f%%"),
        },
    )
    with_data = table.index[table["median"].notna()].tolist()
    if not with_data:
        st.info(
            t(
                "No data for {nights} nights: collect prices for this stay length first.",
                nights=nights,
            )
        )
        return None
    selected = event.selection.rows[0] if event.selection.rows else _start_row(table, last)
    row = table.loc[selected]
    if pd.isna(row["median"]):
        st.info(
            t(
                "No data for {nights} nights on {date}: pick another date.",
                nights=nights,
                date=row["check_in"],
            )
        )
        return None
    st.caption(
        t(
            "Simulating check-in **{date}** ({count} comparables) · prices from search "
            "`{search}` captured {captured}.",
            date=row["check_in"],
            count=int(row["comparables"]),
            search=str(row["search_id"])[:8],
            captured=format_datetime(datetime.fromisoformat(str(row["captured"]))),
        )
    )
    if _newer_attempt_failed(row):
        st.warning(
            t(
                "The latest search for this date failed ({when}): showing the previous valid "
                "capture.",
                when=format_datetime(datetime.fromisoformat(str(row["attempted_at"]))),
            ),
            icon=":material/history:",
        )
    return row


def _keep_choice() -> Keep:
    labels = {
        Keep.BASE.value: t("my base rate (change the discount)"),
        Keep.DISCOUNT.value: t("my discount (change the base rate)"),
    }
    choice = st.radio(
        t("When adjusting, keep"), list(labels), format_func=labels.__getitem__, horizontal=True
    )
    return Keep(choice)


# --- simulator ------------------------------------------------------------------------


def _discount_label(nights: int) -> str:
    kind = discount_kind(nights)
    if kind == "monthly":
        return t("Monthly discount")
    if kind == "weekly":
        return t("Weekly discount")
    return t("Discount")


def _simulate(
    inputs: SimulationInputs, row: pd.Series, target_margin: float, currency: str
) -> tuple[list[Scenario], dict[str, str]]:
    """Scenarios plus their display names (scenario name -> translated label)."""
    median = float(row["median"])
    scenarios = [current_scenario(inputs), aligned_scenario(inputs, median, "Aligned with median")]
    names = {
        scenarios[0].name: t("Current"),
        scenarios[1].name: t("Aligned with median"),
    }
    margin = margin_scenario(inputs, target_margin)
    if margin is not None:
        scenarios.append(margin)
        names[margin.name] = t("Floor for {pct:g}% margin", pct=target_margin)
    section(
        t("Simulation"),
        t(
            "{nights}-night stay, {paid:g} paid nights per 30-day month, with the fees and "
            "costs of the Earnings calculator.",
            nights=inputs.nights,
            paid=inputs.paid_nights,
        ),
    )
    discount_label = _discount_label(inputs.nights)
    accommodation_label = t(
        "{accommodation} ({nights} nights)", accommodation=t(ACCOMMODATION), nights=inputs.nights
    )
    rows: dict[str, list[str]] = {}
    for s in scenarios:
        e = s.economics
        gap = (s.nightly_after_discount / median - 1) * 100
        values = {
            t(BASE_RATE): format_money(s.pricing.nightly_price, currency),
            discount_label: f"{s.pricing.discount_pct:.1f}%",
            t(NIGHTLY_AFTER_DISCOUNT): format_money(s.nightly_after_discount, currency),
            t("vs comparables median"): f"{gap:+.0f}%",
            accommodation_label: format_money(e.booking.accommodation, currency),
            t(GUEST_TOTAL): format_money(e.booking.guest_pays, currency),
            t(HOST_PAYOUT): format_money(e.booking.payout_after_vat, currency),
            t("Margin per stay"): format_money(e.margin_per_stay, currency),
            t("Margin per committed night"): format_money(e.margin_per_committed_night, currency),
            t("Monthly margin"): f"{e.margin_pct:.0f}%",
        }
        for label, value in values.items():
            rows.setdefault(label, []).append(value)
    st.dataframe(
        pd.DataFrame(rows, index=[names[s.name] for s in scenarios]).T,
        width="stretch",
    )
    current, aligned = scenarios[0], scenarios[1]
    insight(
        t(
            "Your nightly price after discount is **{gap:+.0f}%** versus the comparables "
            "median. To align: base nightly rate **{base}** with **{discount:.1f}%** "
            "{discount_label}, leaving a **{margin:.0f}%** monthly margin.",
            gap=(current.nightly_after_discount / median - 1) * 100,
            base=format_money(aligned.pricing.nightly_price, currency),
            discount=aligned.pricing.discount_pct,
            discount_label=discount_label.lower(),
            margin=aligned.economics.margin_pct,
        )
    )
    if not finance_settings.operating_costs(finance_settings.saved_inputs()).fixed_monthly:
        caveat(
            t(
                "No costs saved yet: margins only deduct Airbnb's fees. Add your costs in the "
                "Earnings calculator."
            )
        )
    return scenarios, names


# --- what to enter in Airbnb -------------------------------------------------------------


def _setup_sheet(
    settings: AppSettings,
    scenarios: list[Scenario],
    names: dict[str, str],
    row: pd.Series,
    nights: int,
) -> None:
    currency = settings.scraping.currency
    mine = settings.my_listing
    section(t("Set it up in Airbnb"), t("Current → proposed, ready to apply."))
    options = {s.name: s for s in scenarios[1:]}
    choice = st.radio(
        t("Apply"),
        list(options),
        format_func=names.__getitem__,
        horizontal=True,
        key="setup_apply",
    )
    proposed = options[str(choice)]
    min_stay = st.number_input(
        t("Proposed minimum stay (nights)"),
        1,
        365,
        int(mine.min_nights or nights),
        key="setup_min",
    )
    p = proposed.pricing
    nights_unit = t("{count} nights")
    sheet = pd.DataFrame(
        [
            (
                t(BASE_RATE),
                format_money(float(mine.nightly_price or 0), currency),
                format_money(p.nightly_price, currency),
            ),
            (
                t("Weekly discount (7+ nights)"),
                f"{mine.weekly_discount_pct:g}%",
                f"{p.weekly_discount_pct:.1f}%",
            ),
            (
                t("Monthly discount (28+ nights)"),
                f"{mine.monthly_discount_pct:g}%",
                f"{p.monthly_discount_pct:.1f}%",
            ),
            (
                t("Cleaning fee"),
                format_money(float(mine.cleaning_fee or 0), currency),
                format_money(p.cleaning_fee, currency),
            ),
            (
                t("Minimum stay"),
                nights_unit.format(count=mine.min_nights or "—"),
                nights_unit.format(count=min_stay),
            ),
        ],
        columns=[t("Setting"), t("Current"), t("Proposed")],
    )
    st.dataframe(sheet, hide_index=True, width="stretch")

    booking = proposed.economics.booking
    with st.container(border=True):
        st.markdown(
            t("**Verify the {nights}-night quote in Airbnb before applying**", nights=nights)
        )
        st.markdown(
            t(
                "In Airbnb, open your calendar, select check-in **{date}** for {nights} nights "
                "and check the price preview. With the proposed settings you should see:\n"
                "- {accommodation_label}: **{accommodation}**\n"
                "- {guest_label}: **{guest}**\n"
                "- {payout_label}: **{payout}**",
                date=row["check_in"],
                nights=nights,
                accommodation_label=t(ACCOMMODATION),
                accommodation=format_money(booking.accommodation, currency),
                guest_label=t(GUEST_TOTAL),
                guest=format_money(booking.guest_pays, currency),
                payout_label=t(HOST_PAYOUT),
                payout=format_money(booking.payout_after_vat, currency),
            )
        )
        caveat(
            t(
                "Small differences are normal: Airbnb rounds and may add its own monthly "
                "savings, which it pays for."
            )
        )
    if st.button(t("Save the proposal as my current pricing"), icon=":material/save:"):
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
        st.toast(t("Saved. Apply the same values in Airbnb."), icon=":material/check_circle:")


_SEARCH_STATUS = {
    "complete": "Complete",
    "partial": "Partial",
    "empty": "No listings returned",
    "failed": "Failed",
}


def _newer_attempt_failed(row: pd.Series) -> bool:
    if row.get("attempt_status") != "failed":
        return False
    captured = row.get("captured")
    return pd.isna(captured) or str(row["attempted_at"]) > str(captured)


def _search_note(row: pd.Series) -> str:
    """Status of the latest search of a date, and whether an older capture is shown."""
    if _newer_attempt_failed(row):
        return t("⚠ Failed · previous capture") if pd.notna(row["captured"]) else t("⚠ Failed")
    status = row.get("attempt_status")
    return t(_SEARCH_STATUS[status]) if isinstance(status, str) else "—"


def _start_row(table: pd.DataFrame, last: dict[str, Any]) -> Any:
    """The remembered check-in if it still has data, else the next date with data."""
    with_data = table.index[table["median"].notna()]
    check_ins = table.loc[with_data, "check_in"].astype(str).str[:10]
    remembered_check_in = str(last.get("check_in") or "")
    if remembered_check_in:
        upcoming = check_ins[check_ins >= remembered_check_in]
        if not upcoming.empty:
            return upcoming.index[0]
    return with_data[0]
