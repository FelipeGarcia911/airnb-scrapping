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
from airbnb_tracker.interface.web.i18n import t
from airbnb_tracker.interface.web.theme import CORAL, INK

# Keys are analytics.QUADRANTS names (English); they are translated when shown.
_QUADRANT_COLORS = {
    "Premium, well rated": INK,
    "Expensive for its rating": "#C13515",
    "Good value": "#008A05",
    "Budget": "#B0B0B0",
}
# Lowercase forms used inside sentences.
_QUADRANT_LOWER = {
    "Premium, well rated": "premium, well rated",
    "Expensive for its rating": "expensive for its rating",
    "Good value": "good value",
    "Budget": "budget",
}
_SUMMARY_COLUMNS = ("Competitors", "Median price", "Median rating")


def render() -> None:
    st.title(t("Positioning"))
    st.caption(t("Market analytics"))
    st.markdown(
        t(
            "Where each competitor sits on **price versus quality**, to spot who is "
            "overpriced, who offers the best value and where your listing fits."
        )
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
        t("Value map"),
        t(
            "Each dot is a competitor: rating (quality) against its nightly price for "
            "{nights}-night stays. The lines are the comp set medians.",
            nights=nights,
        ),
    )
    if frame.empty or "Quadrant" not in frame:
        st.info(t("Not enough rated and priced competitors yet."))
        return
    price_mid, rating_mid = frame.attrs["price_mid"], frame.attrs["rating_mid"]
    comps = frame[~frame["is_mine"]]
    plotted = comps.assign(quadrant_label=comps["Quadrant"].map(t))
    rating, price, reviews = (
        t("Guest rating"),
        t("Nightly price ({currency})", currency=currency),
        t("Reviews"),
    )
    points = (
        alt.Chart(plotted)
        .mark_circle(opacity=0.8)
        .encode(
            x=alt.X("rating:Q", title=rating, scale=alt.Scale(zero=False)),
            y=alt.Y("nightly:Q", title=price),
            size=alt.Size("review_count:Q", title=reviews, scale=alt.Scale(range=[30, 400])),
            color=alt.Color("quadrant_label:N", title=t("Quadrant"),
                            scale=alt.Scale(domain=[t(q) for q in _QUADRANT_COLORS],
                                            range=list(_QUADRANT_COLORS.values()))),
            tooltip=[alt.Tooltip("title:N", title=t("Listing")),
                     alt.Tooltip("rating:Q", title=rating),
                     alt.Tooltip("review_count:Q", title=reviews),
                     alt.Tooltip("nightly:Q", title=price, format=",.0f"),
                     alt.Tooltip("quadrant_label:N", title=t("Quadrant"))],
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
            .encode(
                x="rating:Q",
                y="nightly:Q",
                tooltip=[alt.Tooltip("title:N", title=t("Listing")),
                         alt.Tooltip("rating:Q", title=rating)],
            )
        )  # fmt: skip
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
    summary = summary.rename(index=t, columns={c: t(c) for c in _SUMMARY_COLUMNS})
    summary.index.name = t("Quadrant")
    st.dataframe(
        summary,
        width="stretch",
        column_config={t("Median price"): st.column_config.NumberColumn(format="localized")},
    )

    good_value = comps[comps["Quadrant"] == "Good value"].sort_values("rating", ascending=False)
    text = t(
        "**{count}** competitors are *good value* (rated above {rating:.2f} and priced below "
        "{price}): they are the ones guests compare you with first.",
        count=counts.get("Good value", 0),
        rating=rating_mid,
        price=format_money(price_mid, currency),
    )
    if not good_value.empty:
        text += t(" The strongest is **{title}**.", title=good_value.iloc[0]["title"])
    if not mine.empty and "Quadrant" in mine:
        quadrant = str(mine.iloc[0]["Quadrant"])
        text += t(
            " Your listing would fall in **{quadrant}**.",
            quadrant=t(_QUADRANT_LOWER.get(quadrant, quadrant)),
        )
    else:
        text += t(" Your listing has no current price, so it is not plotted.")
    insight(text)
    how_to_read(
        t(
            "- **Right side** = better rated, **top** = more expensive.\n"
            "- *Expensive for its rating* (top left): priced above the median with "
            "below-median ratings; they are vulnerable.\n"
            "- *Good value* (bottom right): the strongest competition on price-for-quality.\n"
            "- Bigger dots have more reviews, so their rating is more reliable."
        )
    )


def _visibility() -> None:
    section(
        t("Search visibility"),
        t("Which listings Airbnb shows first, and what they have in common."),
    )
    st.info(
        t(
            "Not available yet. With *complete coverage* the area is split into many smaller "
            "searches, so the stored positions are not Airbnb's real ranking. A future update "
            "will record the true rank of the first results page to power this view."
        ),
        icon=":material/schedule:",
    )
    caveat(t("Kept out on purpose: showing positions that are not real rankings would mislead."))
