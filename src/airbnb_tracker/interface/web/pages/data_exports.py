"""Step 4 · Export: download every table or write them to the exports folder."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from airbnb_tracker.interface.web.data import load_market
from airbnb_tracker.interface.web.formatting import format_number
from airbnb_tracker.interface.web.guards import require_settings
from airbnb_tracker.interface.web.i18n import t
from airbnb_tracker.interface.web.navigation import next_step
from airbnb_tracker.interface.web.preferences import Preferences, preferences
from airbnb_tracker.interface.web.status import runner_for, start_update

_TABLES = {
    "listings": "One row per listing: location, layout, rating, amenities, minimum stay, "
    "recent reviews. Yours is marked is_mine.",
    "pricing_snapshots": "One row per listing and search: stay dates, nights, original and "
    "discounted price and position among the results kept. Full history.",
    "calendar_snapshots": "One row per listing and day: bookable or not, minimum nights. "
    "Every collection is kept, so availability can be compared over time.",
    "reviews": "One row per review: date, rating and language (no names or texts).",
    "search_runs": "One row per search: parameters, listings found and kept, whether "
    "Airbnb truncated the results. Use it to check coverage.",
}


def render() -> None:
    st.title(t("Export"))
    st.caption(t("Setup & data"))
    st.markdown(
        t(
            "Everything collected so far, ready for Excel, Google Sheets or pandas. "
            "Data is never overwritten: each update adds to the history."
        )
    )
    settings = require_settings()
    if settings is None:
        return

    data = load_market(settings, None)
    if data is None:
        st.info(t("Nothing to export yet: collect data in step 2 first."), icon=":material/sync:")
        next_step("collect", t("Collect data"))
        return

    for name, description in _TABLES.items():
        frame = data.raw_tables[name]  # downloads are always the full, unfiltered data
        with st.container(border=True):
            left, right = st.columns([4, 1], vertical_alignment="center")
            left.markdown(f"**{name}.csv** · " + t("{rows} rows", rows=format_number(len(frame))))
            left.caption(t(description))
            right.download_button(
                t("Download"),
                csv_bytes(frame, preferences()),
                file_name=f"{name}.csv",
                mime="text/csv",
                icon=":material/download:",
                key=f"download_{name}",
                disabled=frame.empty,
                width="stretch",
            )

    runner = runner_for(settings)
    with st.container(border=True):
        st.markdown(f"**{t('Save all files to a folder')}**")
        st.caption(
            t(
                "Writes the five CSV files to `{folder}`. The full history also lives in the "
                "database `{database}` (SQLite).",
                folder=settings.storage.exports_dir,
                database=settings.storage.database_path,
            )
        )
        if st.button(
            t("Write files"), icon=":material/folder:", disabled=runner.busy, key="write_files"
        ):
            start_update(settings, "files")
            st.toast(t("Writing files… follow it in the sidebar."), icon=":material/folder:")
            st.rerun()


_DATE_COLUMNS = ("date", "check_in", "check_out")


def csv_bytes(frame: pd.DataFrame, prefs: Preferences) -> bytes:
    """CSV with the separator and date format of Settings (UTF-8 with BOM, for Excel).

    Dates are rewritten only in date columns; timestamps (``*_at``) stay ISO 8601.
    """
    out = frame.copy()
    for column in _DATE_COLUMNS:
        if column in out.columns:
            dates = pd.to_datetime(out[column], errors="coerce")
            out[column] = dates.dt.strftime(prefs.csv_date_format).where(dates.notna(), None)
    return out.to_csv(index=False, sep=prefs.csv_separator).encode("utf-8-sig")
