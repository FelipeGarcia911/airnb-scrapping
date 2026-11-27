"""Building blocks shared by the analysis pages, so every dashboard reads the same way:
what it shows → chart or table → how to read it → the takeaway → caveats."""

from __future__ import annotations

from collections.abc import Iterable

import streamlit as st

from airbnb_tracker.interface.web.i18n import t
from airbnb_tracker.interface.web.preferences import preferences


def section(title: str, what: str) -> None:
    st.subheader(title)
    st.caption(what)


def how_to_read(text: str) -> None:
    with st.expander(t("How to read this"), icon=":material/help:"):
        st.markdown(text)


def insight(text: str) -> None:
    """The plain-language conclusion of a dashboard."""
    st.info(text, icon=":material/lightbulb:")


def caveat(text: str) -> None:
    st.caption(f":gray[:material/info: {text}]")


def nights_picker(
    available: Iterable[object],
    *,
    key: str,
    label: str | None = None,
    preferred: int | None = None,
) -> int:
    """Pick a stay length among those collected.

    Starts on ``preferred`` (e.g. the last one used), else the default stay length of
    Settings, else the longest collected.
    """
    options = sorted({int(str(n)) for n in available})
    candidates = (preferred, preferences().default_nights)
    default = next((n for n in candidates if n in options), options[-1])
    choice = st.segmented_control(
        label or t("Stay length"),
        options,
        default=default,
        format_func=lambda n: t("{nights} nights", nights=n),
        key=key,
    )
    return int(choice) if choice is not None else default
