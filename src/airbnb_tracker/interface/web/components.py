"""Building blocks shared by the analysis pages, so every dashboard reads the same way:
what it shows → chart or table → how to read it → the takeaway → caveats."""

from __future__ import annotations

from collections.abc import Iterable

import streamlit as st

DEFAULT_NIGHTS = 30


def section(title: str, what: str) -> None:
    st.subheader(title)
    st.caption(what)


def how_to_read(text: str) -> None:
    with st.expander("How to read this", icon=":material/help:"):
        st.markdown(text)


def insight(text: str) -> None:
    """The plain-language conclusion of a dashboard."""
    st.info(text, icon=":material/lightbulb:")


def caveat(text: str) -> None:
    st.caption(f":gray[:material/info: {text}]")


def nights_picker(available: Iterable[object], *, key: str, label: str = "Stay length") -> int:
    """Pick a stay length among those collected (30 nights by default)."""
    options = sorted({int(str(n)) for n in available})
    default = DEFAULT_NIGHTS if DEFAULT_NIGHTS in options else options[-1]
    choice = st.segmented_control(
        label,
        options,
        default=default,
        format_func=lambda n: f"{n} nights",
        key=key,
    )
    return int(choice) if choice is not None else default
