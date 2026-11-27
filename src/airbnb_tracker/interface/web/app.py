"""Streamlit entry point. Start it with ``airbnb-tracker ui``.

Organised around the admin's decision: **Pricing** (what base rate and discount to set,
and what margin it leaves) is the home screen; **Market analytics** explains the market;
**Data & settings** holds the setup, data collection, data quality and exports. The app
opens on the setup until there is data. The sidebar only shows the data status.
"""

from __future__ import annotations

import streamlit as st

from airbnb_tracker.interface.web import navigation, status
from airbnb_tracker.interface.web.context import settings_or_none
from airbnb_tracker.interface.web.data import database_version
from airbnb_tracker.interface.web.pages import (
    availability,
    collect,
    comp_set,
    data_exports,
    data_quality,
    earnings,
    overview,
    positioning,
    price_setup,
    prices,
    pricing_advisor,
    setup,
)
from airbnb_tracker.interface.web.theme import apply_page_style, header


def _has_data() -> bool:
    settings = settings_or_none()
    return settings is not None and database_version(settings) is not None


def main() -> None:
    st.set_page_config(page_title="Competitor Tracker", page_icon="🏠", layout="wide")
    apply_page_style()

    ready = _has_data()
    page = st.Page
    pages = {
        # Pricing
        "price_setup": page(price_setup.render, title="Price & configure",
                            icon=":material/tune:", url_path="price", default=ready),
        "advisor": page(pricing_advisor.render, title="Pricing advisor",
                        icon=":material/price_check:", url_path="pricing-advisor"),
        "earnings": page(earnings.render, title="Earnings calculator",
                         icon=":material/calculate:", url_path="earnings"),
        # Market analytics
        "overview": page(overview.render, title="Market overview", icon=":material/home:",
                         url_path="overview"),
        "comp_set": page(comp_set.render, title="Comparables & amenities",
                         icon=":material/holiday_village:", url_path="comp-set"),
        "prices": page(prices.render, title="Prices", icon=":material/payments:",
                       url_path="prices"),
        "availability": page(availability.render, title="Observed availability",
                             icon=":material/calendar_month:", url_path="availability"),
        "positioning": page(positioning.render, title="Positioning",
                            icon=":material/scatter_plot:", url_path="positioning"),
        # Data & settings
        "setup": page(setup.render, title="Set up my market", icon=":material/settings:",
                      url_path="setup", default=not ready),
        "collect": page(collect.render, title="Collect data", icon=":material/sync:",
                        url_path="collect"),
        "quality": page(data_quality.render, title="Data quality", icon=":material/rule:",
                        url_path="data-quality"),
        "export": page(data_exports.render, title="Export", icon=":material/download:",
                       url_path="export"),
    }  # fmt: skip
    navigation.register(pages)

    with st.sidebar:
        header("Competitor Tracker", "Price your listing with market data.")
    current = st.navigation(
        {
            "Pricing": [pages["price_setup"], pages["advisor"], pages["earnings"]],
            "Market analytics": [
                pages["overview"],
                pages["comp_set"],
                pages["prices"],
                pages["availability"],
                pages["positioning"],
            ],
            "Data & settings": [
                pages["setup"],
                pages["collect"],
                pages["quality"],
                pages["export"],
            ],
        }
    )
    status.sidebar_status()
    current.run()


main()
