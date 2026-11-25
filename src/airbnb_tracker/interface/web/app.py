"""Streamlit entry point. Start it with ``airbnb-tracker ui``.

Organised like market-intelligence dashboards: an Overview with the key numbers,
analysis pages (prices, comp set, availability), and setup / data pages. Updating the
data is a sidebar action that stays visible, with live progress, while you browse.
"""

from __future__ import annotations

import streamlit as st

from airbnb_tracker.interface.web import navigation, status
from airbnb_tracker.interface.web.pages import (
    availability,
    comp_set,
    data_exports,
    overview,
    prices,
    setup,
)
from airbnb_tracker.interface.web.theme import apply_page_style, header


def main() -> None:
    st.set_page_config(page_title="Competitor Tracker", page_icon="🏠", layout="wide")
    apply_page_style()

    pages = {
        "overview": st.Page(overview.render, title="Overview", icon=":material/home:",
                            url_path="overview", default=True),
        "prices": st.Page(prices.render, title="Prices", icon=":material/payments:",
                          url_path="prices"),
        "comp_set": st.Page(comp_set.render, title="Comp set",
                            icon=":material/holiday_village:", url_path="comp-set"),
        "availability": st.Page(availability.render, title="Availability",
                                icon=":material/calendar_month:", url_path="availability"),
        "setup": st.Page(setup.render, title="Set up my market", icon=":material/tune:",
                         url_path="setup"),
        "data": st.Page(data_exports.render, title="Data & exports",
                        icon=":material/download:", url_path="data"),
    }  # fmt: skip
    navigation.register(pages)

    with st.sidebar:
        header("Competitor Tracker", "Your short-stay market, at a glance.")
    current = st.navigation(
        {
            "": [pages["overview"]],
            "Analysis": [pages["prices"], pages["comp_set"], pages["availability"]],
            "Settings & data": [pages["setup"], pages["data"]],
        }
    )
    status.sidebar_card()
    current.run()


main()
