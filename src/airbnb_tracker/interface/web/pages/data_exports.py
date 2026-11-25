"""Data & exports: download every table, run partial updates and read the update log."""

from __future__ import annotations

import streamlit as st

from airbnb_tracker.interface.web.data import load_market
from airbnb_tracker.interface.web.guards import require_settings
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
    st.title("Data & exports")
    st.markdown(
        "Everything collected so far, ready for Excel, Google Sheets or pandas. "
        "Data is never overwritten: each update adds to the history."
    )
    settings = require_settings()
    if settings is None:
        return

    data = load_market(settings, None)
    st.subheader("Download")
    if data is None:
        st.caption("Nothing to download yet: run **Update market data** first.")
    else:
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
        st.caption(f"The database with the full history is at `{settings.storage.database_path}`.")

    runner = runner_for(settings)
    st.subheader("Partial updates")
    st.caption(
        "Normally **Update market data** does everything. Use these to refresh one part only, "
        "for example calendars every couple of days."
    )
    c1, c2, c3 = st.columns(3)
    actions = [
        (c1, "prices", "Update prices only", "Price searches for every stay length and date."),
        (c2, "details", "Update details only", "Listing pages, calendars and reviews."),
        (c3, "files", "Write export files", f"CSV files into `{settings.storage.exports_dir}`."),
    ]
    for column, kind, label, help_text in actions:
        with column.container(border=True, height="stretch"):
            if st.button(label, disabled=runner.busy, width="stretch", key=f"partial_{kind}"):
                start_update(settings, kind)
                st.rerun()
            st.caption(help_text)

    st.subheader("Update log")
    job = runner.current
    if job is None:
        logs = runner.past_logs()
        if not logs:
            st.caption("No updates run from this app yet.")
            return
        chosen = st.selectbox("Previous updates", logs, format_func=lambda p: p.stem)
        st.code(chosen.read_text(encoding="utf-8", errors="replace")[-20000:], language=None,
                height=320)  # fmt: skip
        return
    st.caption(f"{job.label} · {'running' if job.running else 'finished'}")
    st.code(job.log() or "Starting…", language=None, height=320)
