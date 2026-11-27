"""Step 2 · Collect data: run the update and follow it live."""

from __future__ import annotations

import streamlit as st

from airbnb_tracker.infrastructure.config import AppSettings
from airbnb_tracker.interface.web.guards import require_settings
from airbnb_tracker.interface.web.i18n import t
from airbnb_tracker.interface.web.jobs import Job, JobRunner
from airbnb_tracker.interface.web.navigation import next_step
from airbnb_tracker.interface.web.status import (
    last_update_text,
    live_job,
    progress_panel,
    run_estimate,
    runner_for,
    start_update,
)


def render() -> None:
    st.title(t("Collect data"))
    st.caption(t("Setup & data"))
    st.markdown(
        t(
            "Searches Airbnb with your settings and stores the results. Each update adds to "
            "the history, so running it every few days shows how prices and availability move."
        )
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
            st.caption(t("You can switch to other steps or close this page: the update continues."))
        else:
            _start_controls(settings, runner, job)
        _log(job)

    live_job(runner, render_job)


def _what_it_does(settings: AppSettings) -> None:
    run = run_estimate(settings)
    with st.container(border=True):
        st.markdown(f"**{t('What an update does')}**")
        c1, c2, c3 = st.columns(3)
        with c1:
            st.markdown(f"**{t('1 · Prices')}**")
            st.caption(
                t(
                    "{searches} searches: every stay length for every check-in date. "
                    "≈ {minutes} min",
                    searches=run.searches,
                    minutes=f"{run.scan_minutes:.0f}",
                )
            )
        with c2:
            st.markdown(f"**{t('2 · Listing details')}**")
            st.caption(
                t(
                    "Page, calendar and reviews of {count} listings (yours first). ≈ {minutes} min",
                    count=run.listings_to_enrich,
                    minutes=f"{run.enrich_minutes:.0f}",
                )
            )
        with c3:
            st.markdown(f"**{t('3 · Export files')}**")
            st.caption(
                t(
                    "CSV files written to `{folder}`. A few seconds.",
                    folder=settings.storage.exports_dir,
                )
            )


def _start_controls(settings: AppSettings, runner: JobRunner, job: Job | None) -> None:
    if job is not None:
        if job.succeeded:
            st.success(t("{job}: finished. Your results are ready.", job=t(job.label)),
                       icon=":material/check_circle:")  # fmt: skip
        else:
            st.error(t("{job}: stopped or failed. Check the log below.", job=t(job.label)),
                     icon=":material/error:")  # fmt: skip
    run = run_estimate(settings)
    st.caption(last_update_text(settings))
    if st.button(
        t("Start update  ·  ≈ {minutes} min", minutes=f"{run.total_minutes:.0f}"),
        type="primary",
        icon=":material/sync:",
        disabled=runner.busy,
        key="start_everything",
    ):
        start_update(settings, "everything")
        st.rerun()

    with st.expander(t("Update only one part")):
        st.caption(
            t(
                "Useful between full updates, for example refreshing calendars every couple of "
                "days. Listing pages and reviews that are still fresh are skipped automatically."
            )
        )
        c1, c2, c3 = st.columns(3)
        parts = [
            (c1, "prices", t("Prices only")),
            (c2, "details", t("Listing details only")),
            (c3, "files", t("Export files only")),
        ]
        for column, kind, label in parts:
            if column.button(label, disabled=runner.busy, width="stretch", key=f"part_{kind}"):
                start_update(settings, kind)
                st.rerun()

    if job is not None and job.succeeded:
        next_step("price_setup", t("Next: price & configure"))


def _log(job: Job | None) -> None:
    if job is None:
        return
    with st.expander(t("Log"), expanded=False):
        st.code(job.log() or t("Starting…"), language=None, height=300)
