"""Page registry, so pages can link to each other without importing each other."""

from __future__ import annotations

import streamlit as st
from streamlit.navigation.page import StreamlitPage

_PAGES: dict[str, StreamlitPage] = {}


def register(pages: dict[str, StreamlitPage]) -> None:
    _PAGES.update(pages)


def link(name: str, label: str | None = None, icon: str | None = None) -> None:
    page = _PAGES.get(name)
    if page is not None:
        st.page_link(page, label=label or page.title, icon=icon)
