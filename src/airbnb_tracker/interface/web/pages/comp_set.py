"""Comp set: who your competitors are, where they are and what they offer."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from airbnb_tracker.interface.web import analytics
from airbnb_tracker.interface.web.components import caveat, insight, nights_picker, section
from airbnb_tracker.interface.web.guards import require_data, require_settings
from airbnb_tracker.interface.web.i18n import t
from airbnb_tracker.interface.web.theme import CORAL, MUTED

# Columns of analytics.amenity_premium (English keys) shown to the user.
_AMENITY_COLUMNS = (
    "Amenity",
    "Competitors with it",
    "You",
    "Median price with",
    "Median price without",
    "Price difference (same bedrooms)",
    "Sample (with / without)",
    "Confidence",
)
_LISTING_COLUMNS = {
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


def render() -> None:
    st.title(t("Comp set"))
    st.caption(t("Market analytics"))
    st.markdown(
        t(
            "The listings you compete with: the nearest stays matching your filters. "
            "Your listing is shown in coral."
        )
    )
    settings = require_settings()
    if settings is None:
        return
    data = require_data(settings)
    if data is None:
        return

    only_analysed = st.toggle(
        t("Only competitors with full details"),
        value=True,
        help=t("Off: also show every listing that appeared in price searches."),
    )
    listings = data.analysed if only_analysed else data.listings
    listings = _with_median_prices(listings, data.tables["pricing_snapshots"])

    _map(listings, settings.search.radius_km)
    _amenities(data.listings, data.tables["pricing_snapshots"], settings.scraping.currency)
    _table(listings)


def _map(listings: pd.DataFrame, radius_km: float) -> None:
    st.subheader(t("Map"))
    points = listings.dropna(subset=["latitude", "longitude"]).copy()
    if points.empty:
        st.caption(t("No locations available."))
        return
    points["color"] = points["is_mine"].map({True: CORAL, False: MUTED})
    points["size"] = points["is_mine"].map({True: 90, False: 40})
    st.map(points, latitude="latitude", longitude="longitude", color="color", size="size",
           height=400)  # fmt: skip
    st.caption(t("{count} listings · search radius {km:g} km", count=len(points), km=radius_km))


def _amenities(listings: pd.DataFrame, pricing: pd.DataFrame, currency: str) -> None:
    del currency  # amounts are shown in the table with the app's number format
    section(
        t("Amenities and what they are worth"),
        t(
            "For amenities that usually move prices: how many competitors offer each, "
            "whether you do, and the median nightly price of competitors with and without it."
        ),
    )
    nights = nights_picker(pricing["nights"].unique(), key="amenity_nights",
                           label=t("Compare prices for"))  # fmt: skip
    table = analytics.amenity_premium(listings, analytics.nightly_by_listing(pricing, nights))
    if table.empty:
        st.caption(t("Amenities appear after the listing details are collected."))
        return
    view = table.copy()
    view["Amenity"] = view["Amenity"].map(t)
    view["Confidence"] = view["Confidence"].map(lambda v: t(v) if isinstance(v, str) else v)
    view = view.rename(columns={c: t(c) for c in _AMENITY_COLUMNS})
    amount = st.column_config.NumberColumn(format="localized")
    st.dataframe(
        view,
        hide_index=True,
        width="stretch",
        column_config={
            t("Competitors with it"): st.column_config.ProgressColumn(
                format="%.0f%%", min_value=0, max_value=100
            ),
            t("Median price with"): amount,
            t("Median price without"): amount,
            t("Price difference (same bedrooms)"): st.column_config.NumberColumn(format="%+.0f%%"),
        },
    )
    priced = table.dropna(subset=["Price difference (same bedrooms)"])
    priced = priced[priced["Confidence"] == "Good"]
    missing = table[(table["You"] == "✗") & (table["Competitors with it"] >= 50)]
    parts = []
    if not priced.empty:
        top = priced.iloc[0]
        parts.append(
            t(
                "Compared with listings of the same size, those with **{amenity}** charge "
                "about **{difference:+.0f}%** per night.",
                amenity=t(str(top["Amenity"])).lower(),
                difference=top["Price difference (same bedrooms)"],
            )
        )
    if not missing.empty:
        names = ", ".join(t(str(name)).lower() for name in missing["Amenity"].head(4))
        parts.append(
            t(
                "Most competitors offer **{amenities}** and you do not: worth considering if "
                "you can add them.",
                amenities=names,
            )
        )
    if parts:
        insight(" ".join(parts))
    caveat(
        t(
            "The difference compares listings with the same number of bedrooms, but it is "
            "still an association, not a cause (they may also be newer or better located). "
            "It needs at least 5 listings with and 5 without the amenity."
        )
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
    medians.columns = [t("Nightly · {nights}n", nights=n) for n in medians.columns]
    return listings.merge(medians, left_on="listing_id", right_index=True, how="left")


def _table(listings: pd.DataFrame) -> None:
    st.subheader(t("Listings"))
    st.caption(
        t(
            "Median nightly price per stay length across the dates searched. Click Open "
            "to see the listing on Airbnb."
        )
    )
    columns = {key: t(label) for key, label in _LISTING_COLUMNS.items()}
    nightly_prefix = t("Nightly · {nights}n", nights="\x00").split("\x00")[0]
    price_columns = [c for c in listings.columns if str(c).startswith(nightly_prefix)]
    view = listings[[c for c in columns if c in listings] + price_columns].rename(columns=columns)
    st.dataframe(
        view.sort_values([t("Yours"), t("Km")], ascending=[False, True]),
        hide_index=True,
        width="stretch",
        height=440,
        column_config={
            t("Airbnb"): st.column_config.LinkColumn(display_text=t("Open")),
            t("Km"): st.column_config.NumberColumn(format="%.1f"),
            **dict.fromkeys(price_columns, st.column_config.NumberColumn(format="localized")),
        },
    )
