"""Comp set: who your competitors are, where they are and what they offer."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from airbnb_tracker.interface.web.guards import require_data, require_settings
from airbnb_tracker.interface.web.theme import CORAL, MUTED

_TOP_AMENITIES = 25


def render() -> None:
    st.title("Comp set")
    st.caption("Step 3 of 4 · Results")
    st.markdown(
        "The listings you compete with: the nearest stays matching your filters. "
        "Your listing is shown in coral."
    )
    settings = require_settings()
    if settings is None:
        return
    data = require_data(settings)
    if data is None:
        return

    only_analysed = st.toggle(
        "Only competitors with full details",
        value=True,
        help="Off: also show every listing that appeared in price searches.",
    )
    listings = data.analysed if only_analysed else data.listings
    listings = _with_median_prices(listings, data.tables["pricing_snapshots"])

    _map(listings, settings.search.radius_km)
    _amenities(data.analysed)
    _table(listings)


def _map(listings: pd.DataFrame, radius_km: float) -> None:
    st.subheader("Map")
    points = listings.dropna(subset=["latitude", "longitude"]).copy()
    if points.empty:
        st.caption("No locations available.")
        return
    points["color"] = points["is_mine"].map({True: CORAL, False: MUTED})
    points["size"] = points["is_mine"].map({True: 90, False: 40})
    st.map(points, latitude="latitude", longitude="longitude", color="color", size="size",
           height=400)  # fmt: skip
    st.caption(f"{len(points)} listings · search radius {radius_km:g} km")


def _amenities(analysed: pd.DataFrame) -> None:
    st.subheader("What competitors offer")
    st.caption(
        "Share of competitors listing each amenity. Popular amenities you lack are "
        "the easiest improvements; rare ones you have are selling points."
    )
    if analysed.empty or analysed["amenities"].isna().all():
        st.caption("Amenities appear after the listing details are collected.")
        return
    competitors = analysed[~analysed["is_mine"]]
    mine = analysed[analysed["is_mine"]]
    my_amenities = (
        set(mine["amenities"].iloc[0]) if len(mine) and mine["amenities"].iloc[0] else set()
    )
    counts = competitors["amenities"].dropna().explode().value_counts()
    total = competitors["amenities"].notna().sum()
    table = pd.DataFrame(
        {
            "Amenity": counts.index,
            "Competitors with it": (counts.to_numpy() / total * 100).round(0),
            "You have it": ["✓" if a in my_amenities else "✗" for a in counts.index]
            if my_amenities
            else ["?"] * len(counts),
        }
    ).head(_TOP_AMENITIES)
    st.dataframe(
        table,
        hide_index=True,
        width="stretch",
        column_config={
            "Competitors with it": st.column_config.ProgressColumn(
                format="%.0f%%", min_value=0, max_value=100
            )
        },
    )


def _with_median_prices(listings: pd.DataFrame, pricing: pd.DataFrame) -> pd.DataFrame:
    if listings.empty or pricing.empty:
        return listings
    medians = (
        pricing.groupby(["listing_id", "nights"])["nightly_price"]
        .median()
        .round(0)
        .astype("Int64")
        .unstack("nights")
    )
    medians.columns = [f"Nightly · {n}n" for n in medians.columns]
    return listings.merge(medians, left_on="listing_id", right_index=True, how="left")


def _table(listings: pd.DataFrame) -> None:
    st.subheader("Listings")
    st.caption("Median nightly price per stay length across the dates searched. Click Open "
               "to see the listing on Airbnb.")  # fmt: skip
    columns = {
        "is_mine": "Yours",
        "title": "Listing",
        "distance_km": "Km",
        "bedrooms": "Bedrooms",
        "bathrooms": "Baths",
        "max_guests": "Guests",
        "rating": "Rating",
        "review_count": "Reviews",
        "minimum_nights": "Min. nights",
        "is_superhost": "Superhost",
        "url": "Airbnb",
    }
    price_columns = [c for c in listings.columns if c.startswith("Nightly · ")]
    view = listings[[c for c in columns if c in listings] + price_columns].rename(columns=columns)
    st.dataframe(
        view.sort_values(["Yours", "Km"], ascending=[False, True]),
        hide_index=True,
        width="stretch",
        height=440,
        column_config={
            "Airbnb": st.column_config.LinkColumn(display_text="Open"),
            "Km": st.column_config.NumberColumn(format="%.1f"),
            **dict.fromkeys(price_columns, st.column_config.NumberColumn(format="localized")),
        },
    )
