"""Empty states: explain what is missing and what to do next, instead of blank pages."""

from __future__ import annotations

import streamlit as st

from airbnb_tracker.infrastructure.config import AppSettings
from airbnb_tracker.interface.web.context import settings_or_none
from airbnb_tracker.interface.web.data import MarketData, load_market
from airbnb_tracker.interface.web.navigation import link, next_step


def require_settings() -> AppSettings | None:
    settings = settings_or_none()
    if settings is None:
        st.info(
            "Your market is not set up yet. Start by telling the tracker which listing is "
            "yours and what counts as a competitor.",
            icon=":material/info:",
        )
        link("setup", "Set up my market", ":material/tune:")
    return settings


def require_data(settings: AppSettings, my_price: float | None = None) -> MarketData | None:
    data = load_market(settings, my_price)
    if data is None or not data.has_prices:
        st.info(
            "No data for your current settings yet. Collect it in step 2: it searches "
            "prices, reads competitor details and keeps the history.",
            icon=":material/sync:",
        )
        next_step("collect", "Collect data")
        return None
    outlier_notice(data)
    return data


def outlier_notice(data: MarketData) -> None:
    count = len(data.bundle.outliers)
    if not count:
        return
    plural = "listing" if count == 1 else "listings"
    if data.bundle.outliers_excluded:
        st.caption(
            f":material/filter_alt: {count} {plural} with outlier prices left out of these "
            "numbers (see **Data quality**)."
        )
    else:
        st.caption(
            f":orange[:material/warning: {count} {plural} with outlier prices included in "
            "these numbers.]"
        )
