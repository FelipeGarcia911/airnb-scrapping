"""Empty states: explain what is missing and what to do next, instead of blank pages."""

from __future__ import annotations

import streamlit as st

from airbnb_tracker.infrastructure.config import AppSettings
from airbnb_tracker.interface.web.context import settings_or_none
from airbnb_tracker.interface.web.data import MarketData, load_market
from airbnb_tracker.interface.web.navigation import link


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
            "No market data for the current settings yet. Click **Update market data** in "
            "the left panel: it searches prices, reads competitor details and keeps the "
            "history. You can keep browsing while it runs.",
            icon=":material/sync:",
        )
        return None
    return data
