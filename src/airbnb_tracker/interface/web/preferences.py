"""App preferences (Settings → App preferences), saved in the ``ui`` section of config.yaml.

They change how things are shown, never the data: regional format, time zone, start
page, default stay length, remembering the last selection, export format, theme and
density. The language is handled by :mod:`i18n` and lives in the same section.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields, replace
from enum import StrEnum
from typing import Any

import streamlit as st

from airbnb_tracker.infrastructure.config import (
    ConfigurationError,
    read_raw_config,
    save_raw_config,
)
from airbnb_tracker.interface.web.context import config_path

SECTION = "ui"
_SESSION_KEY = "_preferences"


class Region(StrEnum):
    """Number and date conventions (money keeps its currency code next to it)."""

    COLOMBIA = "es-CO"  # 150.000 · 4,8 · 27 nov 2026
    UNITED_STATES = "en-US"  # 150,000 · 4.8 · Nov 27, 2026

    @property
    def thousands(self) -> str:
        return "." if self is Region.COLOMBIA else ","

    @property
    def decimal(self) -> str:
        return "," if self is Region.COLOMBIA else "."


class StartPage(StrEnum):
    PRICING = "price"
    ADVISOR = "pricing-advisor"
    OVERVIEW = "overview"


class Theme(StrEnum):
    SYSTEM = "system"
    LIGHT = "light"
    DARK = "dark"


class Density(StrEnum):
    COMFORTABLE = "comfortable"
    COMPACT = "compact"


@dataclass(frozen=True)
class Preferences:
    region: Region = Region.COLOMBIA
    timezone: str = "America/Bogota"
    start_page: StartPage = StartPage.PRICING
    default_nights: int = 30
    remember_selection: bool = True
    csv_separator: str = ";"
    csv_date_format: str = "%Y-%m-%d"
    theme: Theme = Theme.SYSTEM
    density: Density = Density.COMFORTABLE

    @classmethod
    def from_raw(cls, section: dict[str, Any]) -> Preferences:
        """Unknown or invalid values fall back to the defaults, one by one."""
        defaults = cls()
        values: dict[str, Any] = {}
        for field in fields(cls):
            current = getattr(defaults, field.name)
            given = section.get(field.name, current)
            try:
                values[field.name] = type(current)(given)
            except (TypeError, ValueError):
                values[field.name] = current
        return replace(defaults, **values)

    def to_raw(self) -> dict[str, Any]:
        return {k: (v.value if isinstance(v, StrEnum) else v) for k, v in asdict(self).items()}


def _read_section() -> dict[str, Any]:
    try:
        section = read_raw_config(config_path()).get(SECTION)
    except ConfigurationError:
        return {}
    return dict(section) if isinstance(section, dict) else {}


def preferences() -> Preferences:
    """The preferences of this session (read from config.yaml once)."""
    if _SESSION_KEY not in st.session_state:
        st.session_state[_SESSION_KEY] = Preferences.from_raw(_read_section())
    prefs: Preferences = st.session_state[_SESSION_KEY]
    return prefs


def save_preferences(prefs: Preferences) -> None:
    """Use ``prefs`` from now on; keep the language and any other ``ui`` keys as they are."""
    st.session_state[_SESSION_KEY] = prefs
    try:
        raw = read_raw_config(config_path())
    except ConfigurationError:
        return  # not set up yet: they last for this session
    section = raw.get(SECTION)
    raw[SECTION] = {**(section if isinstance(section, dict) else {}), **prefs.to_raw()}
    save_raw_config(raw, config_path())
