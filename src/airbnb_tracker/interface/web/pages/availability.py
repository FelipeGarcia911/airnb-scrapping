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
from airbnb_tracker.interface.web.i18n import t
from airbnb_tracker.interface.web.theme import CORAL, INK

WINDOWS = (30, 60, 90)
_HORIZON_DAYS = 180
MONTHLY_NIGHTS = 20
_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_SEGMENT_COLUMNS = ("Segment", "Competitors", "Median nightly price",
                    "Not bookable · next 60 days", "Median rating",
                    "Reviews (last 90 days, median)")  # fmt: skip
_PERIOD_COLUMNS = ("From", "To", "Days", "Not bookable")


def _window(days: int) -> str:
    return t("Next {days} days", days=days)


def _day(value: date) -> str:
    """A short date such as "Mar 14" (or "14 mar" in Spanish)."""
    return t("{month} {day}", month=t(_MONTHS[value.month - 1]), day=value.day)


def _month(period: str) -> str:
    """ "2026-03" -> "Mar 2026"."""
    year, month = period.split("-")
    return f"{t(_MONTHS[int(month) - 1])} {year}"


def render() -> None:
    st.title(t("Observed availability"))
    st.caption(t("Market analytics"))
    st.markdown(
        t(
            "What competitors' calendars show: nights that cannot be booked, minimum stays and "
            "past activity from reviews. It describes the market; it does not set prices."
        )
    )
    st.caption(
        t(
            "A night that cannot be booked may be a reservation, a block by the host or a stay "
            "restriction, so treat it as an approximation of occupancy, not confirmed bookings. "
            "History builds up with every update."
        )
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
        st.info(t("Calendars appear after the listing details are collected."))
        return
    competitors = per_listing[~per_listing["Yours"]]
    mine = per_listing[per_listing["Yours"]]

    c1, c2, c3, c4 = st.columns(4)
    c1.metric(t("Not bookable · next 30 days"), f"{competitors[_window(30)].median():.0f}%",
              help=t("Median across competitors"))  # fmt: skip
    c2.metric(t("Not bookable · next 90 days"), f"{competitors[_window(90)].median():.0f}%")
    c3.metric(
        t("Typical minimum stay"),
        t("{nights:g} nights", nights=competitors["Min. nights"].median()),
    )
    c4.metric(
        t("Monthly specialists ({nights}+ nights)", nights=MONTHLY_NIGHTS),
        int((competitors["Min. nights"] >= MONTHLY_NIGHTS).sum()),
        help=t("Competitors that only accept long stays: your direct monthly competition."),
    )
    if len(mine):
        row = mine.iloc[0]
        st.caption(
            t(
                "Your listing: {pct:.0f}% not bookable in the next 30 days, minimum stay "
                "{nights:g} nights.",
                pct=row[_window(30)],
                nights=row["Min. nights"],
            )
        )

    section(
        t("Minimum stay required"),
        t("How many competitors require each minimum number of nights."),
    )
    distribution = competitors["Min. nights"].value_counts().sort_index()
    distribution.index.name = t("Min. nights")
    st.bar_chart(distribution.rename(t("Competitors")), height=240, color=CORAL)
    share_short = (competitors["Min. nights"] <= 3).mean() * 100
    long_minimum = len(mine) and mine.iloc[0]["Min. nights"] >= 20
    insight(
        t("**{share:.0f}%** of competitors accept stays of 3 nights or less. ", share=share_short)
        + (
            t(
                "Your minimum of **{nights:g} nights** leaves you competing only for long stays.",
                nights=mine.iloc[0]["Min. nights"],
            )
            if long_minimum
            else t("Short minimum stays capture more bookings but more turnover.")
        )
    )

    _segments(data, settings.scraping.currency)

    section(t("By competitor"), t("Latest calendar of each competitor."))
    view = per_listing.sort_values(["Yours", _window(30)], ascending=[False, False])
    view = view.rename(columns={c: t(c) for c in ("Yours", "Listing", "Min. nights",
                                                  "Reviews (90 days)", "Airbnb")})  # fmt: skip
    st.dataframe(
        view,
        hide_index=True,
        width="stretch",
        height=420,
        column_config={
            **{
                _window(w): st.column_config.ProgressColumn(
                    format="%.0f%%", min_value=0, max_value=100
                )
                for w in WINDOWS
            },
            t("Airbnb"): st.column_config.LinkColumn(display_text=t("Open")),
        },
    )
    _history(data.tables["reviews"], comp_ids, date.today())


def _segments(data: MarketData, currency: str) -> None:
    section(
        t("Short-stay vs monthly competitors"),
        t(
            "Competitors grouped by the minimum stay they require: do monthly specialists "
            "price, fill and perform differently?"
        ),
    )
    pricing = data.tables["pricing_snapshots"]
    nights = nights_picker(pricing["nights"].unique(), key="segments_nights",
                           label=t("Prices for"))  # fmt: skip
    table = analytics.min_stay_segments(
        data.listings,
        analytics.nightly_by_listing(pricing, nights),
        analytics.unavailable_share(data.tables["calendar_snapshots"], date.today(), 60),
    )
    if table.empty:
        st.caption(t("Minimum stays appear after calendars are collected."))
        return
    view = table.assign(Segment=table["Segment"].map(t))
    st.dataframe(
        view.rename(columns={c: t(c) for c in _SEGMENT_COLUMNS}),
        hide_index=True,
        width="stretch",
        column_config={
            t("Median nightly price"): st.column_config.NumberColumn(format="localized"),
            t("Not bookable · next 60 days"): st.column_config.ProgressColumn(
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
                t(
                    "Monthly specialists ({count}) charge **{gap:+.0f}%** per night versus "
                    "short-stay listings ({monthly} vs {short} for {nights}-night stays) and "
                    "have **{monthly_busy:.0f}%** of the next 60 days not bookable vs "
                    "{short_busy:.0f}%.",
                    count=m["Competitors"],
                    gap=gap,
                    monthly=format_money(m["Median nightly price"], currency),
                    short=format_money(s["Median nightly price"], currency),
                    nights=nights,
                    monthly_busy=m["Not bookable · next 60 days"],
                    short_busy=s["Not bookable · next 60 days"],
                )
            )
    caveat(t("Long minimum stays also make calendars look emptier: short gaps cannot be booked."))


def _per_listing(calendar: pd.DataFrame, analysed: pd.DataFrame) -> pd.DataFrame:
    """Latest calendar of each listing, summarised over the next 30/60/90 days.

    Window columns are already translated; the others keep English keys."""
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
        rows[_window(window)] = 100 - available_share * 100
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
    return merged[["Yours", "Listing", *[_window(w) for w in WINDOWS], "Min. nights",
                   "Reviews (90 days)", "Airbnb"]]  # fmt: skip


def _future(calendar: pd.DataFrame, comp_ids: set[str], today: date) -> None:
    section(
        t("Upcoming calendar of the comp set"),
        t(
            "Share of the comp set that cannot be booked, day by day, for the next {days} "
            "days. Darker = busier.",
            days=_HORIZON_DAYS,
        ),
    )
    demand = analytics.future_demand(calendar, comp_ids, today, _HORIZON_DAYS)
    if demand.empty:
        st.info(t("Calendars appear after the listing details are collected (step 2)."))
        return
    frame = demand.assign(date=pd.to_datetime(demand["date"]))
    frame["week"] = frame["date"] - pd.to_timedelta(frame["date"].dt.weekday, unit="D")
    frame["weekday"] = [t(_WEEKDAYS[d]) for d in frame["date"].dt.weekday]
    frame["day"] = [_day(d) for d in frame["date"]]
    frame["week_label"] = [_day(d) for d in frame["week"]]
    not_bookable = t("Not bookable %")
    week_order = [_day(d) for d in sorted(frame["week"].unique())]
    heatmap = (
        alt.Chart(frame)
        .mark_rect(cornerRadius=3)
        .encode(
            x=alt.X("weekday:O", sort=[t(d) for d in _WEEKDAYS], title=None),
            y=alt.Y("week_label:O", sort=week_order, title=None),
            color=alt.Color("unavailable_pct:Q", title=not_bookable,
                            scale=alt.Scale(domain=[0, 100], range=["#FFF1F3", CORAL])),
            tooltip=[alt.Tooltip("day:N", title=t("Date")),
                     alt.Tooltip("unavailable_pct:Q", title=not_bookable),
                     alt.Tooltip("listings:Q", title=t("Calendars"))],
        )
        .properties(height=min(26 * frame["week"].nunique(), 640))
    )  # fmt: skip
    left, right = st.columns([3, 2])
    left.altair_chart(heatmap, width="stretch")

    with right:
        st.markdown(f"**{t('Busiest upcoming periods')}**")
        periods = analytics.busy_periods(demand)
        if periods.empty:
            st.caption(t("No period stands out: demand looks even."))
        else:
            st.dataframe(periods.rename(columns={c: t(c) for c in _PERIOD_COLUMNS}),
                         hide_index=True, width="stretch",
                         column_config={t("Not bookable"): st.column_config.ProgressColumn(
                             format="%.0f%%", min_value=0, max_value=100)})  # fmt: skip
        st.markdown(f"**{t('By month')}**")
        monthly = analytics.demand_by_month(demand)
        monthly.index = [_month(str(p)) for p in monthly.index]
        st.bar_chart(monthly.rename(not_bookable), height=200, color=CORAL, sort=False)

    if not periods.empty:
        top = periods.iloc[0]
        insight(
            t(
                "The busiest stretch ahead is **{start} – {end}**: {pct:.0f}% of the comp set's "
                "nights are not bookable. This describes competitors' calendars only: it is not "
                "a forecast of your bookings.",
                start=_day(top["From"]),
                end=_day(top["To"]),
                pct=top["Not bookable"],
            )
        )
    caveat(
        t(
            "Not bookable includes bookings, host blocks and minimum-stay gaps, so this is an "
            "approximation. Near dates always look fuller than far ones."
        )
    )


def _history(reviews: pd.DataFrame, comp_ids: set[str], today: date) -> None:
    section(
        t("Past activity from reviews"),
        t(
            "Reviews written each month by guests of the comp set, over the last two years. "
            "Guests review right after their stay, so this traces completed stays."
        ),
    )
    monthly, index = analytics.review_seasonality(reviews, comp_ids, today)
    if monthly.empty or monthly.sum() == 0:
        st.info(t("Reviews appear after the listing details are collected (step 2)."))
        return
    index = index.rename(index=t)
    monthly = monthly.rename(index=_month)
    left, right = st.columns([3, 2])
    left.bar_chart(monthly.rename(t("Reviews")), height=260, color=INK, sort=False)
    month, value = t("Month"), t("Index")
    index_frame = pd.DataFrame({month: list(index.index), value: list(index.values)})
    chart = (
        alt.Chart(index_frame)
        .mark_bar(cornerRadiusEnd=4)
        .encode(
            x=alt.X(f"{month}:N", sort=list(index.index), title=None),
            y=alt.Y(f"{value}:Q", title=t("× average month")),
            color=alt.condition(alt.datum[value] >= 1, alt.value(CORAL), alt.value("#C8C8C8")),
            tooltip=[month, value],
        )
        .properties(height=260)
    )
    right.altair_chart(chart, width="stretch")

    strong = ", ".join(index.sort_values(ascending=False).head(3).index)
    weak = ", ".join(index.sort_values().head(3).index)
    insight(
        t(
            "Over the last two years the comp set received the most reviews in **{strong}** "
            "and the fewest in **{weak}**. Reviews follow completed stays, so this shows when "
            "guests stayed, not what they paid.",
            strong=strong,
            weak=weak,
        )
    )
    how_to_read(
        t(
            "- **Left**: reviews per month (a proxy for completed stays).\n"
            "- **Right**: each calendar month compared with an average month: 1.3 means 30% "
            "more stays than usual.\n"
            "- Not every guest leaves a review, so read the **pattern**, not the absolute "
            "numbers."
        )
    )
