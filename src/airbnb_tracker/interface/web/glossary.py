"""The four amounts every pricing screen uses, with one name each."""

from __future__ import annotations

import streamlit as st

BASE_RATE = "Base nightly rate"
NIGHTLY_AFTER_DISCOUNT = "Nightly after discount"
ACCOMMODATION = "Accommodation after discount"
GUEST_TOTAL = "Estimated guest total"
HOST_PAYOUT = "Estimated host payout"

_DEFINITIONS = (
    f"- **{BASE_RATE}**: the price per night you publish on Airbnb, before discounts.\n"
    f"- **{NIGHTLY_AFTER_DISCOUNT}**: the base rate after your weekly or monthly discount. "
    "Competitor prices are captured this way, so this is the figure to compare.\n"
    f"- **{ACCOMMODATION}**: nights × nightly after discount, for the whole stay.\n"
    f"- **{GUEST_TOTAL}**: accommodation after discount plus the cleaning fee (taxes the "
    "guest may pay are not included).\n"
    f"- **{HOST_PAYOUT}**: what Airbnb pays you after its service fee and the VAT on it.\n\n"
    "Competitor prices exclude cleaning fees, Airbnb's fees and taxes. Airbnb's own monthly "
    "savings (about 2%, paid by Airbnb) are included in them."
)


def glossary() -> None:
    with st.expander("What each amount means", icon=":material/menu_book:"):
        st.markdown(_DEFINITIONS)
