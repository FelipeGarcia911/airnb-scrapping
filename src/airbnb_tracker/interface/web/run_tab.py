"""Run tab: start scan / enrich / export in the background and follow their progress."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import streamlit as st

from airbnb_tracker.infrastructure.config import AppSettings, ConfigurationError, load_settings
from airbnb_tracker.interface.web.estimates import estimate
from airbnb_tracker.interface.web.jobs import Job, JobRunner

_REFRESH_SECONDS = 2


@st.cache_resource
def job_runner(log_dir: str) -> JobRunner:
    """One runner per server process, shared across reruns and browser tabs."""
    return JobRunner(Path(log_dir))


def render(config_path: Path) -> None:
    try:
        settings = load_settings(config_path)
    except ConfigurationError as error:
        st.info(f"Save a valid configuration in **Search setup** first. ({error})")
        return

    runner = job_runner(str(settings.storage.database_path.parent / "logs"))
    run = estimate(settings, len(settings.stays.resolve(date.today())))

    st.subheader("Collect data")
    st.caption("Steps run one at a time in the background: you can switch tabs or close this page.")
    scan, enrich, export, everything = st.columns(4)
    with scan.container(border=True):
        st.markdown("**1 · Prices**")
        st.caption(f"{run.searches} searches · ≈ {run.scan_minutes:.0f} min")
        if st.button("Scan prices", disabled=runner.busy, width="stretch", key="run_scan"):
            _start(runner, "Scanning prices", config_path, ["scan"])

    with enrich.container(border=True):
        st.markdown("**2 · Details**")
        st.caption(f"{run.listings_to_enrich} listings · ≈ {run.enrich_minutes:.0f} min")
        parts = st.pills(
            "Data",
            ["details", "calendars", "reviews"],
            selection_mode="multi",
            default=["details", "calendars", "reviews"],
            label_visibility="collapsed",
        )
        if st.button(
            "Enrich listings", disabled=runner.busy or not parts, width="stretch", key="run_enrich"
        ):
            args = ["enrich", *(arg for part in parts for arg in ("--only", part))]
            _start(runner, "Enriching listings", config_path, args)

    with export.container(border=True):
        st.markdown("**3 · Files**")
        fmt = st.segmented_control("Format", ["csv", "json"], default="csv", key="export_format")
        if st.button("Export", disabled=runner.busy, width="stretch", key="run_export"):
            _start(runner, "Exporting", config_path, ["export", "-f", fmt or "csv"])

    with everything.container(border=True):
        st.markdown("**All steps**")
        st.caption(f"Scan, enrich and export · ≈ {run.total_minutes:.0f} min")
        if st.button(
            "Run everything",
            type="primary",
            disabled=runner.busy,
            width="stretch",
            key="run_all",
        ):
            _start(runner, "Running all steps", config_path, ["run", "-f", "csv"])

    _job_panel(runner, settings)


def _start(runner: JobRunner, label: str, config_path: Path, args: list[str]) -> None:
    runner.start(label, config_path, args)
    st.rerun()


def _job_panel(runner: JobRunner, settings: AppSettings) -> None:
    # Poll only while something runs, so an idle page does not keep refreshing.
    interval = _REFRESH_SECONDS if runner.busy else None

    @st.fragment(run_every=interval)
    def panel() -> None:
        job = runner.current
        if job is None:
            st.caption(f"Data is stored in `{settings.storage.database_path}`.")
            return
        with st.container(border=True):
            _job_status(job)
            if job.running and st.button("Stop", type="secondary", key="stop_job"):
                runner.stop()
            with st.expander("Log", expanded=job.running):
                st.code(job.log() or "Starting…", language=None, height=280)
        if not job.running and st.session_state.get("job_seen") != job.log_path.name:
            st.session_state["job_seen"] = job.log_path.name
            st.rerun()  # re-enable buttons and refresh results once the job finishes

    panel()


def _job_status(job: Job) -> None:
    minutes, seconds = divmod(int(job.elapsed_seconds()), 60)
    if job.running:
        progress = job.progress()
        text = f"{job.label} · {minutes}:{seconds:02d}"
        if progress:
            done, total = progress
            st.progress(done / total, text=f"{text} · {done}/{total}")
        else:
            st.progress(0, text=f"{text} · starting")
    elif job.succeeded:
        st.success(f"{job.label}: done. See the **Results** tab.", icon=":material/check_circle:")
    else:
        st.error(f"{job.label}: stopped or failed. Check the log.", icon=":material/error:")
