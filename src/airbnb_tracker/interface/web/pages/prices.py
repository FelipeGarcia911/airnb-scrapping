"""Prices: what the comp set charges per stay length and season, and where you stand."""

from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st

from airbnb_tracker.application.dto import DurationReport
from airbnb_tracker.interface.web import analytics
from airbnb_tracker.interface.web.components import caveat, insight, section
from airbnb_tracker.interface.web.data import my_price_input
from airbnb_tracker.interface.web.formatting import format_money
from airbnb_tracker.interface.web.guards import require_data, require_settings
from airbnb_tracker.interface.web.theme import CORAL, INK

# Whole amounts with thousands separators (the browser's locale decides the separator).
_AMOUNT = st.column_config.NumberColumn(format="localized")
_POSITION = {"below_market": "Below market", "competitive": "Competitive", "premium": "Premium"}


def render() -> None:
    st.title("Prices")
    st.caption("Market analytics")
    st.markdown(
        "What comparable stays charge **per night after discount**, for each stay length you "
        "track: the stay total after their discounts, divided by the nights, before cleaning "
        "fee, Airbnb's fees and taxes."
    )
    settings = require_settings()
    if settings is None:
        return
    currency = settings.scraping.currency
    price = my_price_input(settings)
    data = require_data(settings, price)
    if data is None:
        return

    with st.expander("How to read this table", icon=":material/help:"):
        st.markdown(
            "- **P25 / Median / P75**: a quarter of competitors charge less than P25, half "
            "less than the median, three quarters less than P75. **P25–P75 is the "
            "competitive range.**\n"
            "- **Monthly**: the median nightly price × 30, useful for long stays.\n"
            "- **Discount**: typical discount and share of listings giving one "
            "(weekly from 7 nights, monthly from 28).\n"
            "- **Your position**: below P25 = below market, P25–P75 = competitive, "
            "above P75 = premium."
        )

    frame = pd.DataFrame([_row(d) for d in data.bundle.durations])
    st.dataframe(
        frame,
        hide_index=True,
        width="stretch",
        column_config={
            "P25": _AMOUNT,
            "Median": _AMOUNT,
            "P75": _AMOUNT,
            "Monthly": _AMOUNT,
            "Discount": st.column_config.NumberColumn(format="%.0f%%"),
            "With discount": st.column_config.ProgressColumn(
                format="%.0f%%", min_value=0, max_value=100
            ),
        },
    )
    distance_to_month = [abs(int(n) - 30) for n in frame["Nights"]]
    focus = frame.iloc[distance_to_month.index(min(distance_to_month))]
    p25, p75 = analytics.as_float(focus["P25"]), analytics.as_float(focus["P75"])
    insight(
        f"For **{focus['Nights']}-night stays**, half of the comp set charges between "
        f"**{format_money(p25, currency)}** and **{format_money(p75, currency)}** per night; "
        f"the typical price is **{format_money(analytics.as_float(focus['Median']), currency)}**."
    )

    pricing = data.tables["pricing_snapshots"]
    _length_of_stay(pricing, data.listings, currency)
    _seasonality(pricing)


def _row(report: DurationReport) -> dict[str, object]:
    dist = report.distribution
    median = round(dist.median.amount) if dist else None
    return {
        "Nights": report.nights,
        "Listings": report.listings,
        "P25": round(dist.p25.amount) if dist else None,
        "Median": median,
        "P75": round(dist.p75.amount) if dist else None,
        "Monthly": median * 30 if median else None,
        "Discount": float(report.median_discount_pct) if report.median_discount_pct else None,
        "With discount": report.discounted_share_pct,
        "Your position": (
            f"{_POSITION[report.position.value]} · above {report.percentile:.0f}%"
            if report.position and report.percentile is not None
            else "—"
        ),
    }


def _length_of_stay(pricing: pd.DataFrame, listings: pd.DataFrame, currency: str) -> None:
    section(
        "Length-of-stay discounts",
        "How the nightly price falls as stays get longer. Long-stay guests compare monthly "
        "totals, so this is where monthly pricing is won or lost.",
    )
    comp_ids = set(analytics.competitors(listings)["listing_id"]) or None
    curve = analytics.los_curve(pricing, comp_ids)
    if len(curve) < 2:
        st.caption("Collect at least two stay lengths to compare them.")
        return
    chart_data = curve.reset_index()[["nights", "P25", "Median", "P75"]].melt(
        "nights", var_name="Band", value_name="Nightly"
    )
    chart = (
        alt.Chart(chart_data)
        .mark_line(point=True)
        .encode(
            x=alt.X("nights:O", title="Stay length (nights)"),
            y=alt.Y("Nightly:Q", title=f"Nightly price ({currency})", scale=alt.Scale(zero=False)),
            color=alt.Color("Band:N", scale=alt.Scale(domain=["P25", "Median", "P75"],
                            range=["#B0B0B0", CORAL, INK])),
            tooltip=["nights", "Band", alt.Tooltip("Nightly:Q", format=",.0f")],
        )
        .properties(height=280)
    )  # fmt: skip
    left, right = st.columns([3, 2])
    left.altair_chart(chart, width="stretch")
    right.dataframe(
        curve[["Median", "Change vs shortest", "Listings"]],
        width="stretch",
        column_config={
            "Median": _AMOUNT,
            "Change vs shortest": st.column_config.NumberColumn(format="%+.0f%%"),
        },
    )
    shortest, longest = curve.index[0], curve.index[-1]
    change = analytics.as_float(curve["Change vs shortest"].iloc[-1]) or 0.0
    medians = analytics.by_label(curve["Median"])
    monthly = medians[30] * 30 if 30 in medians else None
    insight(
        f"From {shortest} to {longest} nights the typical nightly price changes "
        f"**{change:+.0f}%**."
        + (
            f" A typical month costs about **{format_money(monthly, currency)}**."
            if monthly
            else ""
        )
        + " If your discount for long stays is smaller than the market's, monthly guests will "
        "find you expensive."
    )
    ladder = analytics.los_ladder(pricing, listings)
    if not ladder.empty:
        with st.expander("Discount ladder of each listing"):
            drop_column = ladder.columns[-1]
            st.dataframe(
                ladder.rename(columns={"title": "Listing", "is_mine": "Yours"}),
                hide_index=True,
                width="stretch",
                column_config={
                    **{c: _AMOUNT for c in ladder.columns if c.endswith("nights")},
                    drop_column: st.column_config.NumberColumn(format="%.0f%%"),
                },
            )
            caveat("Listings priced for both the shortest and longest stay lengths only.")


def _seasonality(pricing: pd.DataFrame) -> None:
    st.subheader("By check-in date")
    st.caption(
        "Median nightly price for each check-in date sampled (latest collection). "
        "Higher months are high season."
    )
    if pricing.empty:
        return
    latest = pricing[
        pricing["collected_at"]
        == pricing.groupby(["check_in", "nights"])["collected_at"].transform("max")
    ]
    table = (
        latest.groupby(["check_in", "nights"])["nightly_price"]
        .median()
        .unstack("nights")
        .round(0)
        .astype("Int64")
        .rename(columns=lambda nights: f"{nights} nights")
    )
    st.dataframe(
        table,
        width="stretch",
        column_config=dict.fromkeys(table.columns, _AMOUNT),
    )
    st.line_chart(table, height=260)
    if len(table) > 1:
        overall = analytics.by_label(table.median(axis=1))
        high, low = max(overall, key=overall.__getitem__), min(overall, key=overall.__getitem__)
        insight(
            f"Check-ins on **{high}** get the highest prices and on **{low}** the lowest "
            f"({(overall[high] / overall[low] - 1) * 100:.0f}% apart): that is the seasonal "
            "swing to reflect in your own calendar."
        )
