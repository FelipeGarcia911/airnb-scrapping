"""Regional formatting (money, numbers, dates) and money input shared by every page.

Separators and date style follow Settings → Regional format; money is always whole
amounts with the currency code next to it.

Streamlit's number input cannot show thousands separators, so amounts are typed in a
text field that is reformatted when you leave it ("180000" -> "180.000").
"""

from __future__ import annotations

import re
from datetime import date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import streamlit as st

from airbnb_tracker.interface.web import regional
from airbnb_tracker.interface.web.i18n import t
from airbnb_tracker.interface.web.preferences import preferences

_NON_DIGITS = re.compile(r"\D")


def format_number(value: float | None, decimals: int = 0) -> str:
    """``value`` with the regional separators: 1440.5 -> "1.440,5" (es-CO)."""
    if value is None:
        return "—"
    return regional.number(value, decimals, preferences().region)


def format_pct(value: float | None, decimals: int = 0, *, signed: bool = False) -> str:
    if value is None:
        return "—"
    sign = "+" if signed and value > 0 else ""
    return f"{sign}{format_number(value, decimals)}%"


def format_amount(value: float | None, currency: str) -> str:
    """Whole amount with thousands separators, e.g. 180000 -> "180.000" (es-CO)."""
    del currency  # whole amounts in every currency; kept for call-site readability
    return "" if value is None else format_number(round(value))


def format_money(value: float | None, currency: str) -> str:
    return "—" if value is None else f"${format_amount(value, currency)} {currency.upper()}"


def format_date(value: date | datetime | str | None) -> str:
    """27 nov 2026 (es-CO) or Nov 27, 2026 (en-US)."""
    day = regional.as_date(value)
    return regional.long_date(day, preferences().region) if day else "—"


def local_time(moment: datetime) -> datetime:
    """``moment`` in the time zone of Settings (UTC if the zone is unknown)."""
    try:
        zone = ZoneInfo(preferences().timezone)
    except (ZoneInfoNotFoundError, ValueError):
        zone = ZoneInfo("UTC")
    return moment.astimezone(zone)


def format_datetime(moment: datetime | None) -> str:
    if moment is None:
        return "—"
    local = local_time(moment)
    return f"{format_date(local)} {local:%H:%M}"


def parse_amount(text: str | None) -> int | None:
    """Digits only: "$180.000", "180,000" and "180000" all mean 180000. Empty -> None."""
    digits = _NON_DIGITS.sub("", text or "")
    return int(digits) if digits else None


def money_input(
    label: str,
    *,
    key: str,
    currency: str,
    default: int | None = None,
    help: str | None = None,
    placeholder: str | None = None,
) -> int | None:
    """Amount input with thousands separators. ``st.session_state[key]`` holds the integer."""
    text_key = f"{key}__text"
    if key not in st.session_state:
        st.session_state[key] = default
    if text_key not in st.session_state:
        st.session_state[text_key] = format_amount(st.session_state[key], currency)

    def normalise() -> None:
        amount = parse_amount(st.session_state[text_key])
        st.session_state[key] = amount
        st.session_state[text_key] = format_amount(amount, currency)

    st.text_input(
        f"{label} ({currency.upper()})",
        key=text_key,
        on_change=normalise,
        placeholder=placeholder if placeholder is not None else t("Not set"),
        help=help,
    )
    value: int | None = st.session_state[key]
    return value


def forget_money_input(key: str) -> None:
    """Drop an input's state so it is rebuilt from ``st.session_state[key]`` next run."""
    st.session_state.pop(f"{key}__text", None)
