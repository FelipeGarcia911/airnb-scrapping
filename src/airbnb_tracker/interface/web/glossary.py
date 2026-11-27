"""The four amounts every pricing screen uses, with one name each.

The constants are the English source strings (translation keys): show them with ``t()``.
"""

from __future__ import annotations

import streamlit as st

from airbnb_tracker.interface.web.i18n import t

BASE_RATE = "Base nightly rate"
NIGHTLY_AFTER_DISCOUNT = "Nightly after discount"
ACCOMMODATION = "Accommodation after discount"
GUEST_TOTAL = "Estimated guest total"
HOST_PAYOUT = "Estimated host payout"

_DEFINITIONS = (
    "- **{base}**: the price per night you publish on Airbnb, before discounts.\n"
    "- **{nightly}**: the base rate after your weekly or monthly discount. "
    "Competitor prices are captured this way, so this is the figure to compare.\n"
    "- **{accommodation}**: nights × nightly after discount, for the whole stay.\n"
    "- **{guest}**: accommodation after discount plus the cleaning fee (taxes the "
    "guest may pay are not included).\n"
    "- **{payout}**: what Airbnb pays you after its service fee and the VAT on it.\n\n"
    "Competitor prices exclude cleaning fees, Airbnb's fees and taxes. Airbnb's own monthly "
    "savings (about 2%, paid by Airbnb) are included in them."
)


def glossary() -> None:
    with st.expander(t("What each amount means"), icon=":material/menu_book:"):
        st.markdown(
            t(
                _DEFINITIONS,
                base=t(BASE_RATE),
                nightly=t(NIGHTLY_AFTER_DISCOUNT),
                accommodation=t(ACCOMMODATION),
                guest=t(GUEST_TOTAL),
                payout=t(HOST_PAYOUT),
            )
        )
