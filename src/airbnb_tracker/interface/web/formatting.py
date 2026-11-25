"""Money formatting and input shared by every page: whole amounts, thousands separators.

Streamlit's number input cannot show thousands separators, so amounts are typed in a
text field that is reformatted when you leave it ("180000" -> "180.000").
"""

from __future__ import annotations

import re

import streamlit as st

# Colombian pesos group thousands with dots; other currencies use commas.
_THOUSANDS_SEPARATOR = {"COP": "."}
_NON_DIGITS = re.compile(r"\D")


def format_amount(value: float | None, currency: str) -> str:
    """Whole amount with thousands separators, e.g. 180000 -> "180.000" in COP."""
    if value is None:
        return ""
    separator = _THOUSANDS_SEPARATOR.get(currency.upper(), ",")
    return f"{round(value):,}".replace(",", separator)


def format_money(value: float | None, currency: str) -> str:
    return "—" if value is None else f"${format_amount(value, currency)} {currency.upper()}"


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
    placeholder: str = "Not set",
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
        placeholder=placeholder,
        help=help,
    )
    value: int | None = st.session_state[key]
    return value


def forget_money_input(key: str) -> None:
    """Drop an input's state so it is rebuilt from ``st.session_state[key]`` next run."""
    st.session_state.pop(f"{key}__text", None)
