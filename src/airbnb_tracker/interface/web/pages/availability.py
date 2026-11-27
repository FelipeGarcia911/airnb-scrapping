"""Availability: how booked competitors are, their minimum stays and recent activity."""

from __future__ import annotations

from datetime import date, timedelta

import altair as alt
import pandas as pd
import streamlit as st

from airbnb_tracker.interface.web import analytics
from airbnb_tracker.interface.web.components import (
    caveat,
    how_to_read,
    insight,
    nights_picker,
    section,
)
from airbnb_tracker.interface.web.data import MarketData
from airbnb_tracker.interface.web.formatting import format_money
from airbnb_tracker.interface.web.guards import require_data, require_settings
from airbnb_tracker.interface.web.theme import CORAL, INK

WINDOWS = (30, 60, 90)
_HORIZON_DAYS = 180
MONTHLY_NIGHTS = 20


def render() -> None:
    st.title("Observed availability")
    st.caption("Market analytics")
    st.markdown(
        "What competitors' calendars show: nights that cannot be booked, minimum stays and "
        "past activity from reviews. It describes the market; it does not set prices."
    )
    st.caption(
        "A night that cannot be booked may be a reservation, a block by the host or a stay "
        "restriction, so treat it as an approximation of occupancy, not confirmed bookings. "
        "History builds up with every update."
    )
    settings = require_settings()
    if settings is None:
        return
    data = require_data(settings)
    if data is None:
        return

    comp_ids = set(analytics.competitors(data.listings)["listing_id"])
    _future(data.tables["calendar_snapshots"], comp_ids, date.today())

    per_listing = _per_listing(data.tables["calendar_snapshots"], data.analysed)
    if per_listing.empty:
        st.info("Calendars appear after the listing details are collected.")
        return
    competitors = per_listing[~per_listing["Yours"]]
    mine = per_listing[per_listing["Yours"]]

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Not bookable · next 30 days", f"{competitors['Next 30 days'].median():.0f}%",
              help="Median across competitors")  # fmt: skip
    c2.metric("Not bookable · next 90 days", f"{competitors['Next 90 days'].median():.0f}%")
    c3.metric("Typical minimum stay", f"{competitors['Min. nights'].median():g} nights")
    c4.metric(
        f"Monthly specialists ({MONTHLY_NIGHTS}+ nights)",
        int((competitors["Min. nights"] >= MONTHLY_NIGHTS).sum()),
        help="Competitors that only accept long stays: your direct monthly competition.",
    )
    if len(mine):
        row = mine.iloc[0]
        st.caption(
            f"Your listing: {row['Next 30 days']:.0f}% not bookable in the next 30 days, "
            f"minimum stay {row['Min. nights']:g} nights."
        )

    section("Minimum stay required", "How many competitors require each minimum number of nights.")
    distribution = competitors["Min. nights"].value_counts().sort_index()
    st.bar_chart(distribution.rename("Competitors"), height=240, color=CORAL)
    share_short = (competitors["Min. nights"] <= 3).mean() * 100
    insight(
        f"**{share_short:.0f}%** of competitors accept stays of 3 nights or less. "
        + (f"Your minimum of **{mine.iloc[0]['Min. nights']:g} nights** leaves you competing "
           "only for long stays." if len(mine) and mine.iloc[0]["Min. nights"] >= 20 else
           "Short minimum stays capture more bookings but more turnover.")
    )  # fmt: skip

    _segments(data, settings.scraping.currency)

    section("By competitor", "Latest calendar of each competitor.")
    st.dataframe(
        per_listing.sort_values(["Yours", "Next 30 days"], ascending=[False, False]),
        hide_index=True,
        width="stretch",
        height=420,
        column_config={
            f"Next {w} days": st.column_config.ProgressColumn(
                format="%.0f%%", min_value=0, max_value=100
            )
            for w in WINDOWS
        },
    )
    _history(data.tables["reviews"], comp_ids, date.today())


def _segments(data: MarketData, currency: str) -> None:
    section(
        "Short-stay vs monthly competitors",
        "Competitors grouped by the minimum stay they require: do monthly specialists price, "
        "fill and perform differently?",
    )
    pricing = data.tables["pricing_snapshots"]
    nights = nights_picker(pricing["nights"].unique(), key="segments_nights",
                           label="Prices for")  # fmt: skip
    table = analytics.min_stay_segments(
        data.listings,
        analytics.nightly_by_listing(pricing, nights),
        analytics.unavailable_share(data.tables["calendar_snapshots"], date.today(), 60),
    )
    if table.empty:
        st.caption("Minimum stays appear after calendars are collected.")
        return
    st.dataframe(
        table,
        hide_index=True,
        width="stretch",
        column_config={
            "Median nightly price": st.column_config.NumberColumn(format="localized"),
            "Not bookable · next 60 days": st.column_config.ProgressColumn(
                format="%.0f%%", min_value=0, max_value=100
            ),
        },
    )
    monthly = table[table["Segment"].str.startswith("Monthly")]
    short = table[table["Segment"].str.startswith("Short")]
    if not monthly.empty and not short.empty:
        m, s = monthly.iloc[0], short.iloc[0]
        if m["Median nightly price"] and s["Median nightly price"]:
            gap = (m["Median nightly price"] / s["Median nightly price"] - 1) * 100
            insight(
                f"Monthly specialists ({m['Competitors']}) charge **{gap:+.0f}%** per night "
                f"versus short-stay listings ({format_money(m['Median nightly price'], currency)}"
                f" vs {format_money(s['Median nightly price'], currency)} for {nights}-night "
                f"stays) and have **{m['Not bookable · next 60 days']:.0f}%** of the next 60 "
                f"days not bookable vs {s['Not bookable · next 60 days']:.0f}%."
            )
    caveat("Long minimum stays also make calendars look emptier: short gaps cannot be booked.")


def _per_listing(calendar: pd.DataFrame, analysed: pd.DataFrame) -> pd.DataFrame:
    """Latest calendar of each listing, summarised over the next 30/60/90 days."""
    if calendar.empty:
        return pd.DataFrame()
    frame = calendar.copy()
    frame["collected_at"] = pd.to_datetime(frame["collected_at"])
    frame["date"] = pd.to_datetime(frame["date"])
    latest = frame[
        frame["collected_at"] == frame.groupby("listing_id")["collected_at"].transform("max")
    ]
    start = latest["collected_at"].dt.tz_localize(None).dt.normalize()
    rows = {}
    for window in WINDOWS:
        in_window = latest[(latest["date"] >= start) & (latest["date"] < start + timedelta(window))]
        available_share = in_window.groupby("listing_id")["available"].mean()
        rows[f"Next {window} days"] = 100 - available_share * 100
    upcoming = latest[latest["date"] >= start]
    rows["Min. nights"] = upcoming.groupby("listing_id")["minimum_nights"].agg(
        lambda values: values.mode().iloc[0] if not values.mode().empty else None
    )
    summary = pd.DataFrame(rows).round(0).reset_index()
    labels = {
        "title": "Listing",
        "is_mine": "Yours",
        "reviews_last_90_days": "Reviews (90 days)",
        "url": "Airbnb",
    }
    info = analysed[["listing_id", *labels]].rename(columns=labels)
    merged = info.merge(summary, on="listing_id", how="inner").drop(columns="listing_id")
    return merged[["Yours", "Listing", *[f"Next {w} days" for w in WINDOWS], "Min. nights",
                   "Reviews (90 days)", "Airbnb"]]  # fmt: skip


def _future(calendar: pd.DataFrame, comp_ids: set[str], today: date) -> None:
    section(
        "Upcoming calendar of the comp set",
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
            f"{top['Not bookable']:.0f}% of the comp set's nights are not bookable. "
            "This describes competitors' calendars only: it is not a forecast of your "
            "bookings."
        )
    caveat(
        "Not bookable includes bookings, host blocks and minimum-stay gaps, so this is an "
        "approximation. Near dates always look fuller than far ones."
    )


def _history(reviews: pd.DataFrame, comp_ids: set[str], today: date) -> None:
    section(
        "Past activity from reviews",
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
        f"Over the last two years the comp set received the most reviews in **{strong}** "
        f"and the fewest in **{weak}**. Reviews follow completed stays, so this shows when "
        "guests stayed, not what they paid."
    )
    how_to_read(
        "- **Left**: reviews per month (a proxy for completed stays).\n"
        "- **Right**: each calendar month compared with an average month: 1.3 means 30% "
        "more stays than usual.\n"
        "- Not every guest leaves a review, so read the **pattern**, not the absolute numbers."
    )
