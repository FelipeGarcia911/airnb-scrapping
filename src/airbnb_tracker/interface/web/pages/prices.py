"""Prices: what the comp set charges per stay length and season, and where you stand."""

from __future__ import annotations

import re

import altair as alt
import pandas as pd
import streamlit as st

from airbnb_tracker.application.dto import DurationReport
from airbnb_tracker.interface.web import analytics
from airbnb_tracker.interface.web.components import caveat, insight, section
from airbnb_tracker.interface.web.data import my_price_input
from airbnb_tracker.interface.web.formatting import format_money
from airbnb_tracker.interface.web.guards import require_data, require_settings
from airbnb_tracker.interface.web.i18n import t
from airbnb_tracker.interface.web.theme import CORAL, INK

# Whole amounts with thousands separators (the browser's locale decides the separator).
_AMOUNT = st.column_config.NumberColumn(format="localized")
_POSITION = {"below_market": "Below market", "competitive": "Competitive", "premium": "Premium"}
_DURATION_COLUMNS = ("Nights", "Listings", "P25", "Median", "P75", "Monthly", "Discount",
                     "With discount", "Your position")  # fmt: skip
_DROP = re.compile(r"^Drop (\d+)→(\d+) nights$")
_NIGHTS = re.compile(r"^(\d+) nights$")


def render() -> None:
    st.title(t("Prices"))
    st.caption(t("Market analytics"))
    st.markdown(
        t(
            "What comparable stays charge **per night after discount**, for each stay length "
            "you track: the stay total after their discounts, divided by the nights, before "
            "cleaning fee, Airbnb's fees and taxes."
        )
    )
    settings = require_settings()
    if settings is None:
        return
    currency = settings.scraping.currency
    price = my_price_input(settings)
    data = require_data(settings, price)
    if data is None:
        return

    with st.expander(t("How to read this table"), icon=":material/help:"):
        st.markdown(
            t(
                "- **P25 / Median / P75**: a quarter of competitors charge less than P25, half "
                "less than the median, three quarters less than P75. **P25–P75 is the "
                "competitive range.**\n"
                "- **Monthly**: the median nightly price × 30, useful for long stays.\n"
                "- **Discount**: typical discount and share of listings giving one "
                "(weekly from 7 nights, monthly from 28).\n"
                "- **Your position**: below P25 = below market, P25–P75 = competitive, "
                "above P75 = premium."
            )
        )

    frame = pd.DataFrame([_row(d) for d in data.bundle.durations])
    st.dataframe(
        frame.rename(columns={c: t(c) for c in _DURATION_COLUMNS}),
        hide_index=True,
        width="stretch",
        column_config={
            "P25": _AMOUNT,
            t("Median"): _AMOUNT,
            "P75": _AMOUNT,
            t("Monthly"): _AMOUNT,
            t("Discount"): st.column_config.NumberColumn(format="%.0f%%"),
            t("With discount"): st.column_config.ProgressColumn(
                format="%.0f%%", min_value=0, max_value=100
            ),
        },
    )
    distance_to_month = [abs(int(n) - 30) for n in frame["Nights"]]
    focus = frame.iloc[distance_to_month.index(min(distance_to_month))]
    p25, p75 = analytics.as_float(focus["P25"]), analytics.as_float(focus["P75"])
    insight(
        t(
            "For **{nights}-night stays**, half of the comp set charges between **{low}** and "
            "**{high}** per night; the typical price is **{median}**.",
            nights=focus["Nights"],
            low=format_money(p25, currency),
            high=format_money(p75, currency),
            median=format_money(analytics.as_float(focus["Median"]), currency),
        )
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
            t(
                "{position} · above {pct:.0f}%",
                position=t(_POSITION[report.position.value]),
                pct=report.percentile,
            )
            if report.position and report.percentile is not None
            else "—"
        ),
    }


def _ladder_label(column: str) -> str:
    if match := _DROP.match(column):
        return t("Drop {shortest}→{longest} nights", shortest=match[1], longest=match[2])
    if match := _NIGHTS.match(column):
        return t("{nights} nights", nights=match[1])
    return t({"title": "Listing", "is_mine": "Yours"}.get(column, column))


def _length_of_stay(pricing: pd.DataFrame, listings: pd.DataFrame, currency: str) -> None:
    section(
        t("Length-of-stay discounts"),
        t(
            "How the nightly price falls as stays get longer. Long-stay guests compare monthly "
            "totals, so this is where monthly pricing is won or lost."
        ),
    )
    comp_ids = set(analytics.competitors(listings)["listing_id"]) or None
    curve = analytics.los_curve(pricing, comp_ids)
    if len(curve) < 2:
        st.caption(t("Collect at least two stay lengths to compare them."))
        return
    chart_data = curve.reset_index()[["nights", "P25", "Median", "P75"]].melt(
        "nights", var_name="Band", value_name="Nightly"
    )
    chart_data["Band"] = chart_data["Band"].map(t)
    chart = (
        alt.Chart(chart_data)
        .mark_line(point=True)
        .encode(
            x=alt.X("nights:O", title=t("Stay length (nights)")),
            y=alt.Y("Nightly:Q", title=t("Nightly price ({currency})", currency=currency),
                    scale=alt.Scale(zero=False)),
            color=alt.Color("Band:N", title=t("Band"),
                            scale=alt.Scale(domain=["P25", t("Median"), "P75"],
                                            range=["#B0B0B0", CORAL, INK])),
            tooltip=[alt.Tooltip("nights", title=t("Nights")),
                     alt.Tooltip("Band", title=t("Band")),
                     alt.Tooltip("Nightly:Q", title=t("Nightly"), format=",.0f")],
        )
        .properties(height=280)
    )  # fmt: skip
    left, right = st.columns([3, 2])
    left.altair_chart(chart, width="stretch")
    curve_columns = ("Median", "Change vs shortest", "Listings")
    view = curve[list(curve_columns)].rename(columns={c: t(c) for c in curve_columns})
    view.index.name = t("Nights")
    right.dataframe(
        view,
        width="stretch",
        column_config={
            t("Median"): _AMOUNT,
            t("Change vs shortest"): st.column_config.NumberColumn(format="%+.0f%%"),
        },
    )
    shortest, longest = curve.index[0], curve.index[-1]
    change = analytics.as_float(curve["Change vs shortest"].iloc[-1]) or 0.0
    medians = analytics.by_label(curve["Median"])
    monthly = medians[30] * 30 if 30 in medians else None
    insight(
        t(
            "From {shortest} to {longest} nights the typical nightly price changes "
            "**{change:+.0f}%**.",
            shortest=shortest,
            longest=longest,
            change=change,
        )
        + (
            t(" A typical month costs about **{amount}**.", amount=format_money(monthly, currency))
            if monthly
            else ""
        )
        + t(
            " If your discount for long stays is smaller than the market's, monthly guests "
            "will find you expensive."
        )
    )
    ladder = analytics.los_ladder(pricing, listings)
    if not ladder.empty:
        with st.expander(t("Discount ladder of each listing")):
            drop_column = _ladder_label(str(ladder.columns[-1]))
            view = ladder.rename(columns=lambda c: _ladder_label(str(c)))
            amounts = [_ladder_label(str(c)) for c in ladder.columns if _NIGHTS.match(str(c))]
            st.dataframe(
                view,
                hide_index=True,
                width="stretch",
                column_config={
                    **dict.fromkeys(amounts, _AMOUNT),
                    drop_column: st.column_config.NumberColumn(format="%.0f%%"),
                },
            )
            caveat(t("Listings priced for both the shortest and longest stay lengths only."))


def _seasonality(pricing: pd.DataFrame) -> None:
    st.subheader(t("By check-in date"))
    st.caption(
        t(
            "Median nightly price for each check-in date sampled (latest collection). "
            "Higher months are high season."
        )
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
        .rename(columns=lambda nights: t("{nights} nights", nights=nights))
    )
    table.index.name = t("Check-in")
    table.columns.name = None
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
            t(
                "Check-ins on **{high}** get the highest prices and on **{low}** the lowest "
                "({gap:.0f}% apart): that is the seasonal swing to reflect in your own calendar.",
                high=high,
                low=low,
                gap=(overall[high] / overall[low] - 1) * 100,
            )
        )
