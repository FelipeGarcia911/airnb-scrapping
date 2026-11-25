"""Step 4 · Export: download every table or write them to the exports folder."""

from __future__ import annotations

import streamlit as st

from airbnb_tracker.interface.web.data import load_market
from airbnb_tracker.interface.web.guards import require_settings
from airbnb_tracker.interface.web.navigation import next_step
from airbnb_tracker.interface.web.status import runner_for, start_update

_TABLES = {
    "listings": "One row per listing: location, layout, rating, amenities, minimum stay, "
    "recent reviews. Yours is marked is_mine.",
    "pricing_snapshots": "One row per listing and search: stay dates, nights, original and "
    "discounted price, position in Airbnb's results. Full history.",
    "calendar_snapshots": "One row per listing and day: bookable or not, minimum nights. "
    "Every collection is kept, so availability can be compared over time.",
    "reviews": "One row per review: date, rating and language (no names or texts).",
    "search_runs": "One row per search: parameters, listings found and kept, whether "
    "Airbnb truncated the results. Use it to check coverage.",
}


def render() -> None:
    st.title("Export")
    st.caption("Step 4 of 4")
    st.markdown(
        "Everything collected so far, ready for Excel, Google Sheets or pandas. "
        "Data is never overwritten: each update adds to the history."
    )
    settings = require_settings()
    if settings is None:
        return

    data = load_market(settings, None)
    if data is None:
        st.info("Nothing to export yet: collect data in step 2 first.", icon=":material/sync:")
        next_step("collect", "Go to step 2: collect data")
        return

    for name, description in _TABLES.items():
        frame = data.tables[name]
        with st.container(border=True):
            left, right = st.columns([4, 1], vertical_alignment="center")
            left.markdown(f"**{name}.csv** · {len(frame):,} rows")
            left.caption(description)
            right.download_button(
                "Download",
                frame.to_csv(index=False).encode("utf-8-sig"),
                file_name=f"{name}.csv",
                mime="text/csv",
                icon=":material/download:",
                key=f"download_{name}",
                disabled=frame.empty,
                width="stretch",
            )

    runner = runner_for(settings)
    with st.container(border=True):
        st.markdown("**Save all files to a folder**")
        st.caption(
            f"Writes the five CSV files to `{settings.storage.exports_dir}`. The full history "
            f"also lives in the database `{settings.storage.database_path}` (SQLite)."
        )
        if st.button(
            "Write files", icon=":material/folder:", disabled=runner.busy, key="write_files"
        ):
            start_update(settings, "files")
            st.toast("Writing files… follow it in the sidebar.", icon=":material/folder:")
            st.rerun()
