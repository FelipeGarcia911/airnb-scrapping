"""Positioning: price versus quality across the comp set, and search visibility."""

from __future__ import annotations

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
from airbnb_tracker.interface.web.formatting import format_money
from airbnb_tracker.interface.web.guards import require_data, require_settings
from airbnb_tracker.interface.web.theme import CORAL, INK

_QUADRANT_COLORS = {
    "Premium, well rated": INK,
    "Expensive for its rating": "#C13515",
    "Good value": "#008A05",
    "Budget": "#B0B0B0",
}


def render() -> None:
    st.title("Positioning")
    st.caption("Step 3 of 5 · Market analytics")
    st.markdown(
        "Where each competitor sits on **price versus quality**, to spot who is overpriced, "
        "who offers the best value and where your listing fits."
    )
    settings = require_settings()
    if settings is None:
        return
    data = require_data(settings)
    if data is None:
        return
    pricing = data.tables["pricing_snapshots"]
    nights = nights_picker(pricing["nights"].unique(), key="positioning_nights")
    nightly = analytics.nightly_by_listing(pricing, nights)
    frame = analytics.value_map(data.listings, nightly)
    _value_map(frame, settings.scraping.currency, nights)
    _visibility()


def _value_map(frame: pd.DataFrame, currency: str, nights: int) -> None:
    section(
        "Value map",
        f"Each dot is a competitor: rating (quality) against its nightly price for "
        f"{nights}-night stays. The lines are the comp set medians.",
    )
    if frame.empty or "Quadrant" not in frame:
        st.info("Not enough rated and priced competitors yet.")
        return
    price_mid, rating_mid = frame.attrs["price_mid"], frame.attrs["rating_mid"]
    comps = frame[~frame["is_mine"]]
    points = (
        alt.Chart(comps)
        .mark_circle(opacity=0.8)
        .encode(
            x=alt.X("rating:Q", title="Guest rating", scale=alt.Scale(zero=False)),
            y=alt.Y("nightly:Q", title=f"Nightly price ({currency})"),
            size=alt.Size("review_count:Q", title="Reviews", scale=alt.Scale(range=[30, 400])),
            color=alt.Color("Quadrant:N", scale=alt.Scale(domain=list(_QUADRANT_COLORS),
                            range=list(_QUADRANT_COLORS.values()))),
            tooltip=["title", "rating", "review_count",
                     alt.Tooltip("nightly:Q", format=",.0f"), "Quadrant"],
        )
    )  # fmt: skip
    rules = alt.Chart(pd.DataFrame({"y": [price_mid]})).mark_rule(strokeDash=[4, 4]).encode(
        y="y:Q"
    ) + alt.Chart(pd.DataFrame({"x": [rating_mid]})).mark_rule(strokeDash=[4, 4]).encode(x="x:Q")
    layers = points + rules
    mine = frame[frame["is_mine"]]
    if not mine.empty:
        layers += (
            alt.Chart(mine)
            .mark_point(shape="diamond", size=320, filled=True, color=CORAL)
            .encode(x="rating:Q", y="nightly:Q", tooltip=["title", "rating"])
        )
    st.altair_chart(layers.properties(height=440), width="stretch")

    counts = comps["Quadrant"].value_counts()
    summary = (
        pd.DataFrame(
            {
                "Competitors": counts,
                "Median price": comps.groupby("Quadrant")["nightly"].median().round(0),
                "Median rating": comps.groupby("Quadrant")["rating"].median().round(2),
            }
        )
        .reindex(list(_QUADRANT_COLORS))
        .dropna(how="all")
    )
    st.dataframe(
        summary,
        width="stretch",
        column_config={"Median price": st.column_config.NumberColumn(format="localized")},
    )

    good_value = comps[comps["Quadrant"] == "Good value"].sort_values("rating", ascending=False)
    text = (
        f"**{counts.get('Good value', 0)}** competitors are *good value* (rated above "
        f"{rating_mid:.2f} and priced below {format_money(price_mid, currency)}): they are "
        "the ones guests compare you with first."
    )
    if not good_value.empty:
        text += f" The strongest is **{good_value.iloc[0]['title']}**."
    if not mine.empty and "Quadrant" in mine:
        text += f" Your listing would fall in **{mine.iloc[0]['Quadrant'].lower()}**."
    else:
        text += " Your listing has no current price, so it is not plotted."
    insight(text)
    how_to_read(
        "- **Right side** = better rated, **top** = more expensive.\n"
        "- *Expensive for its rating* (top left): priced above the median with below-median "
        "ratings; they are vulnerable.\n"
        "- *Good value* (bottom right): the strongest competition on price-for-quality.\n"
        "- Bigger dots have more reviews, so their rating is more reliable."
    )


def _visibility() -> None:
    section(
        "Search visibility",
        "Which listings Airbnb shows first, and what they have in common.",
    )
    st.info(
        "Not available yet. With *complete coverage* the area is split into many smaller "
        "searches, so the stored positions are not Airbnb's real ranking. A future update "
        "will record the true rank of the first results page to power this view.",
        icon=":material/schedule:",
    )
    caveat("Kept out on purpose: showing positions that are not real rankings would mislead.")
