"""Demand: which upcoming dates are filling up, and which months are busy historically."""

from __future__ import annotations

from datetime import date

import altair as alt
import pandas as pd
import streamlit as st

from airbnb_tracker.interface.web import analytics
from airbnb_tracker.interface.web.components import caveat, how_to_read, insight, section
from airbnb_tracker.interface.web.guards import require_data, require_settings
from airbnb_tracker.interface.web.theme import CORAL, INK

_HORIZON_DAYS = 180


def render() -> None:
    st.title("Demand")
    st.caption("Step 3 of 4 · Results")
    st.markdown(
        "Two views of demand: **ahead**, from how full competitors' calendars already are, "
        "and **behind**, from when guests left reviews in the last two years."
    )
    settings = require_settings()
    if settings is None:
        return
    data = require_data(settings)
    if data is None:
        return
    comp_ids = set(analytics.competitors(data.listings)["listing_id"])
    today = date.today()
    _future(data.tables["calendar_snapshots"], comp_ids, today)
    _history(data.tables["reviews"], comp_ids, today)


def _future(calendar: pd.DataFrame, comp_ids: set[str], today: date) -> None:
    section(
        "Upcoming demand calendar",
        f"Share of the comp set that cannot be booked, day by day, for the next "
        f"{_HORIZON_DAYS} days. Darker = busier.",
    )
    demand = analytics.future_demand(calendar, comp_ids, today, _HORIZON_DAYS)
    if demand.empty:
        st.info("Calendars appear after the listing details are collected (step 2).")
        return
    frame = demand.assign(date=pd.to_datetime(demand["date"]))
    frame["week"] = frame["date"] - pd.to_timedelta(frame["date"].dt.weekday, unit="D")
    frame["weekday"] = frame["date"].dt.strftime("%a")
    heatmap = (
        alt.Chart(frame)
        .mark_rect(cornerRadius=3)
        .encode(
            x=alt.X("weekday:O", sort=["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
                    title=None),
            y=alt.Y("week:T", title=None, axis=alt.Axis(format="%b %d")),
            color=alt.Color("unavailable_pct:Q", title="Not bookable %",
                            scale=alt.Scale(domain=[0, 100], range=["#FFF1F3", CORAL])),
            tooltip=[alt.Tooltip("date:T", format="%a %b %d, %Y"),
                     alt.Tooltip("unavailable_pct:Q", title="Not bookable %"),
                     alt.Tooltip("listings:Q", title="Calendars")],
        )
        .properties(height=min(26 * frame["week"].nunique(), 640))
    )  # fmt: skip
    left, right = st.columns([3, 2])
    left.altair_chart(heatmap, width="stretch")

    with right:
        st.markdown("**Busiest upcoming periods**")
        periods = analytics.busy_periods(demand)
        if periods.empty:
            st.caption("No period stands out: demand looks even.")
        else:
            st.dataframe(periods, hide_index=True, width="stretch",
                         column_config={"Not bookable": st.column_config.ProgressColumn(
                             format="%.0f%%", min_value=0, max_value=100)})  # fmt: skip
        st.markdown("**By month**")
        monthly = analytics.demand_by_month(demand)
        st.bar_chart(monthly.rename("Not bookable %"), height=200, color=CORAL)

    if not periods.empty:
        top = periods.iloc[0]
        insight(
            f"The busiest stretch ahead is **{top['From']:%b %d} – {top['To']:%b %d}**: "
            f"{top['Not bookable']:.0f}% of the comp set is already not bookable. "
            "Those dates can carry a higher rate; dates still wide open may need a lower "
            "price or shorter minimum stay to fill."
        )
    caveat(
        "Not bookable includes bookings, host blocks and minimum-stay gaps, so this is an "
        "approximation. Near dates always look fuller than far ones."
    )


def _history(reviews: pd.DataFrame, comp_ids: set[str], today: date) -> None:
    section(
        "Seasonality from reviews",
        "Reviews written each month by guests of the comp set, over the last two years. "
        "Guests review right after their stay, so this traces completed stays.",
    )
    monthly, index = analytics.review_seasonality(reviews, comp_ids, today)
    if monthly.empty or monthly.sum() == 0:
        st.info("Reviews appear after the listing details are collected (step 2).")
        return
    left, right = st.columns([3, 2])
    left.bar_chart(monthly.rename("Reviews"), height=260, color=INK)
    index_frame = index.rename("Index").reset_index().rename(columns={"index": "Month"})
    chart = (
        alt.Chart(index_frame)
        .mark_bar(cornerRadiusEnd=4)
        .encode(
            x=alt.X("Month:N", sort=list(index.index), title=None),
            y=alt.Y("Index:Q", title="× average month"),
            color=alt.condition(alt.datum.Index >= 1, alt.value(CORAL), alt.value("#C8C8C8")),
            tooltip=["Month", "Index"],
        )
        .properties(height=260)
    )
    right.altair_chart(chart, width="stretch")

    strong = ", ".join(index.sort_values(ascending=False).head(3).index)
    weak = ", ".join(index.sort_values().head(3).index)
    insight(
        f"Historically the strongest months are **{strong}** and the weakest **{weak}**. "
        "Plan higher rates and longer minimum stays for strong months, and discounts or "
        "flexible stays for weak ones."
    )
    how_to_read(
        "- **Left**: reviews per month (a proxy for completed stays).\n"
        "- **Right**: each calendar month compared with an average month: 1.3 means 30% "
        "more stays than usual.\n"
        "- Not every guest leaves a review, so read the **pattern**, not the absolute numbers."
    )
