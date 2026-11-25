"""Step 2 · Collect data: run the update and follow it live."""

from __future__ import annotations

import streamlit as st

from airbnb_tracker.infrastructure.config import AppSettings
from airbnb_tracker.interface.web.data import ago, last_update
from airbnb_tracker.interface.web.guards import require_settings
from airbnb_tracker.interface.web.jobs import Job, JobRunner
from airbnb_tracker.interface.web.navigation import next_step
from airbnb_tracker.interface.web.status import (
    live_job,
    progress_panel,
    run_estimate,
    runner_for,
    start_update,
)


def render() -> None:
    st.title("Collect data")
    st.caption("Step 2 of 4")
    st.markdown(
        "Searches Airbnb with your settings and stores the results. Each update adds to the "
        "history, so running it every few days shows how prices and availability move."
    )
    settings = require_settings()
    if settings is None:
        return
    runner = runner_for(settings)
    _what_it_does(settings)

    def render_job(job: Job | None) -> None:
        if job and job.running:
            with st.container(border=True):
                progress_panel(job, runner, key="collect")
            st.caption("You can switch to other steps or close this page: the update continues.")
        else:
            _start_controls(settings, runner, job)
        _log(job)

    live_job(runner, render_job)


def _what_it_does(settings: AppSettings) -> None:
    run = run_estimate(settings)
    with st.container(border=True):
        st.markdown("**What an update does**")
        c1, c2, c3 = st.columns(3)
        with c1:
            st.markdown("**1 · Prices**")
            st.caption(
                f"{run.searches} searches: every stay length for every check-in date. "
                f"≈ {run.scan_minutes:.0f} min"
            )
        with c2:
            st.markdown("**2 · Listing details**")
            st.caption(
                f"Page, calendar and reviews of {run.listings_to_enrich} listings (yours "
                f"first). ≈ {run.enrich_minutes:.0f} min"
            )
        with c3:
            st.markdown("**3 · Export files**")
            st.caption(f"CSV files written to `{settings.storage.exports_dir}`. A few seconds.")


def _start_controls(settings: AppSettings, runner: JobRunner, job: Job | None) -> None:
    if job is not None:
        if job.succeeded:
            st.success(f"{job.label}: finished. Your results are ready.",
                       icon=":material/check_circle:")  # fmt: skip
        else:
            st.error(f"{job.label}: stopped or failed. Check the log below.",
                     icon=":material/error:")  # fmt: skip
    run = run_estimate(settings)
    st.caption(f"Last update: {ago(last_update(settings))}")
    if st.button(
        f"Start update  ·  ≈ {run.total_minutes:.0f} min",
        type="primary",
        icon=":material/sync:",
        disabled=runner.busy,
        key="start_everything",
    ):
        start_update(settings, "everything")
        st.rerun()

    with st.expander("Update only one part"):
        st.caption(
            "Useful between full updates, for example refreshing calendars every couple of "
            "days. Listing pages and reviews that are still fresh are skipped automatically."
        )
        c1, c2, c3 = st.columns(3)
        parts = [
            (c1, "prices", "Prices only"),
            (c2, "details", "Listing details only"),
            (c3, "files", "Export files only"),
        ]
        for column, kind, label in parts:
            if column.button(label, disabled=runner.busy, width="stretch", key=f"part_{kind}"):
                start_update(settings, kind)
                st.rerun()

    if job is not None and job.succeeded:
        next_step("overview", "Next: see the results")


def _log(job: Job | None) -> None:
    if job is None:
        return
    with st.expander("Log", expanded=False):
        st.code(job.log() or "Starting…", language=None, height=300)
