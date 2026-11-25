"""Availability: how booked competitors are, their minimum stays and recent activity."""

from __future__ import annotations

from datetime import timedelta

import pandas as pd
import streamlit as st

from airbnb_tracker.interface.web.guards import require_data, require_settings

WINDOWS = (30, 60, 90)
MONTHLY_NIGHTS = 20


def render() -> None:
    st.title("Availability")
    st.caption("Step 3 of 4 · Results")
    st.markdown(
        "From each competitor's calendar: how many upcoming nights cannot be booked and "
        "the minimum stay they require."
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

    st.subheader("Minimum stay required")
    st.caption("How many competitors require each minimum number of nights.")
    distribution = competitors["Min. nights"].value_counts().sort_index()
    st.bar_chart(distribution.rename("Competitors"), height=240)

    st.subheader("By competitor")
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
