"""Prices: what the comp set charges per stay length and season, and where you stand."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from airbnb_tracker.application.dto import DurationReport
from airbnb_tracker.interface.web.data import my_price_input
from airbnb_tracker.interface.web.guards import require_data, require_settings

# Whole amounts with thousands separators (the browser's locale decides the separator).
_AMOUNT = st.column_config.NumberColumn(format="localized")
_POSITION = {"below_market": "Below market", "competitive": "Competitive", "premium": "Premium"}


def render() -> None:
    st.title("Prices")
    st.caption("Step 3 of 4 · Results")
    st.markdown(
        "What comparable stays charge **per night**, for each stay length you track. "
        "Prices are after Airbnb's discounts and before cleaning fee and taxes."
    )
    settings = require_settings()
    if settings is None:
        return
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
    st.subheader("Price range by stay length")
    st.caption("Nightly price: lower quarter, median and upper quarter of the comp set.")
    st.bar_chart(frame, x="Nights", y=["P25", "Median", "P75"], stack=False, height=280)

    _seasonality(data.tables["pricing_snapshots"])


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
