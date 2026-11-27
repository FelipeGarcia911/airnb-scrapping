"""Regional conventions (separators, month names) as pure functions: no Streamlit here."""

from __future__ import annotations

import re
from datetime import date, datetime

from airbnb_tracker.interface.web.preferences import Region

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MONTHS = {
    Region.COLOMBIA: ("ene", "feb", "mar", "abr", "may", "jun",
                      "jul", "ago", "sept", "oct", "nov", "dic"),
    Region.UNITED_STATES: ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
                           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"),
}  # fmt: skip


def localise_digits(text: str, region: Region) -> str:
    """Swap Python's "1,234.5" separators for the region's ("1.234,5" in es-CO)."""
    return text.replace(",", "\0").replace(".", region.decimal).replace("\0", region.thousands)


def number(value: float, decimals: int, region: Region) -> str:
    return localise_digits(f"{value:,.{decimals}f}", region)


def as_date(value: object) -> date | None:
    """A date, datetime or ISO date string ("2026-11-27"); anything else -> None."""
    if isinstance(value, date):
        return value
    text = str(value)[:10]
    if _ISO_DATE.match(text):
        try:
            return date.fromisoformat(text)
        except ValueError:
            return None
    return None


def long_date(day: date, region: Region) -> str:
    """27 nov 2026 (es-CO) or Nov 27, 2026 (en-US)."""
    month = _MONTHS[region][day.month - 1]
    return (
        f"{day.day} {month} {day.year}"
        if region is Region.COLOMBIA
        else (f"{month} {day.day}, {day.year}")
    )


def format_value(value: object, spec: str, region: Region) -> str:
    """How a ``{placeholder}`` is shown: numbers with a spec and dates follow the region."""
    if isinstance(value, bool):
        return format(value, spec)
    if spec and isinstance(value, int | float):
        return localise_digits(format(value, spec), region)
    if not spec and (
        isinstance(value, datetime | date) or (isinstance(value, str) and _ISO_DATE.match(value))
    ):
        day = as_date(value)
        return long_date(day, region) if day else str(value)
    return format(value, spec)
