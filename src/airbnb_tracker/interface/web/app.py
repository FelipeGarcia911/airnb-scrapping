"""Streamlit entry point. Start it with ``airbnb-tracker ui``.

A step-by-step flow, like setup wizards in market tools: 1 · set up the search,
2 · collect data, 3 · read the results, 4 · export. The app opens on step 1 until
there is data, and on the results afterwards. The sidebar shows update progress.
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
    overview,
    prices,
    setup,
)
from airbnb_tracker.interface.web.theme import apply_page_style, header


def _has_data() -> bool:
    settings = settings_or_none()
    return settings is not None and database_version(settings) is not None


def main() -> None:
    st.set_page_config(page_title="Competitor Tracker", page_icon="🏠", layout="wide")
    apply_page_style()

    start_on_results = _has_data()
    pages = {
        "setup": st.Page(setup.render, title="Set up my market", icon=":material/tune:",
                         url_path="setup", default=not start_on_results),
        "collect": st.Page(collect.render, title="Collect data", icon=":material/sync:",
                           url_path="collect"),
        "overview": st.Page(overview.render, title="Overview", icon=":material/home:",
                            url_path="overview", default=start_on_results),
        "prices": st.Page(prices.render, title="Prices", icon=":material/payments:",
                          url_path="prices"),
        "comp_set": st.Page(comp_set.render, title="Comp set",
                            icon=":material/holiday_village:", url_path="comp-set"),
        "availability": st.Page(availability.render, title="Availability",
                                icon=":material/calendar_month:", url_path="availability"),
        "export": st.Page(data_exports.render, title="Export", icon=":material/download:",
                          url_path="export"),
    }  # fmt: skip
    navigation.register(pages)

    with st.sidebar:
        header("Competitor Tracker", "Your short-stay market, step by step.")
    current = st.navigation(
        {
            "Step 1 · Set up": [pages["setup"]],
            "Step 2 · Collect": [pages["collect"]],
            "Step 3 · Results": [
                pages["overview"],
                pages["prices"],
                pages["comp_set"],
                pages["availability"],
            ],
            "Step 4 · Export": [pages["export"]],
        }
    )
    status.sidebar_status()
    current.run()


main()
