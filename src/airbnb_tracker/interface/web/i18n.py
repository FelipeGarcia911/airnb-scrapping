"""Interface translations (i18n), gettext style.

Source strings are written in English and wrapped with :func:`t`; catalogs in
``locales/<language>/*.json`` map each English string to its translation. A missing
translation falls back to English, so a page never breaks because of a new string.

    t("Simulating check-in {date}", date="2027-01-01")

Values are inserted after translating, so translators keep the ``{placeholders}``;
numbers with a format spec (``{rating:.2f}``) and dates use the regional format.
"""

from __future__ import annotations

import json
import string
from enum import StrEnum
from functools import cache
from pathlib import Path

import streamlit as st

from airbnb_tracker.infrastructure.config import (
    ConfigurationError,
    read_raw_config,
    save_raw_config,
)
from airbnb_tracker.interface.web.context import config_path
from airbnb_tracker.interface.web.preferences import preferences
from airbnb_tracker.interface.web.regional import format_value

LANGUAGE_KEY = "_language"
_LOCALES = Path(__file__).parent / "locales"


class Language(StrEnum):
    ENGLISH = "en"
    SPANISH = "es"

    @property
    def label(self) -> str:
        return {Language.ENGLISH: "English", Language.SPANISH: "Español"}[self]


@cache
def catalog(language: Language) -> dict[str, str]:
    """All the translations of a language (every JSON file in its folder, merged)."""
    folder = _LOCALES / language.value
    merged: dict[str, str] = {}
    for path in sorted(folder.glob("*.json")) if folder.exists() else ():
        merged.update(json.loads(path.read_text(encoding="utf-8")))
    return merged


def current_language() -> Language:
    if LANGUAGE_KEY not in st.session_state:
        st.session_state[LANGUAGE_KEY] = _saved_language().value
    return Language(st.session_state[LANGUAGE_KEY])


def t(text: str, /, **values: object) -> str:
    """``text`` in the current language, with ``{placeholders}`` filled from ``values``."""
    language = current_language()
    translated = text if language is Language.ENGLISH else catalog(language).get(text, text)
    return _RegionalFormatter().format(translated, **values) if values else translated


class _RegionalFormatter(string.Formatter):
    """Numbers and dates in ``{placeholders}`` follow Settings → Regional format."""

    def format_field(self, value: object, format_spec: str) -> str:
        return format_value(value, format_spec, preferences().region)


def set_language(language: Language) -> None:
    """Use ``language`` from now on and remember it in config.yaml."""
    st.session_state[LANGUAGE_KEY] = language.value
    try:
        raw = read_raw_config(config_path())
    except ConfigurationError:
        return  # no configuration yet: the choice lasts for this session
    raw.setdefault("ui", {})["language"] = language.value
    save_raw_config(raw, config_path())


def _saved_language() -> Language:
    try:
        value = (read_raw_config(config_path()).get("ui") or {}).get("language")
    except ConfigurationError:
        return Language.ENGLISH
    return Language(value) if value in {lang.value for lang in Language} else Language.ENGLISH
