"""Data updates: starting them, following their progress and the sidebar status badge.

The full controls live in step 2 (Collect data); the sidebar only shows status, so the
progress stays visible while you browse other steps.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from pathlib import Path

import streamlit as st

from airbnb_tracker.application.progress import Phase
from airbnb_tracker.infrastructure.config import AppSettings
from airbnb_tracker.interface.web.context import config_path, settings_or_none
from airbnb_tracker.interface.web.data import ago, database_version, last_update, sample_status
from airbnb_tracker.interface.web.estimates import RunEstimate, estimate
from airbnb_tracker.interface.web.formatting import format_datetime
from airbnb_tracker.interface.web.i18n import t
from airbnb_tracker.interface.web.jobs import PHASE_LABELS, Job, JobRunner
from airbnb_tracker.interface.web.navigation import link

POLL_SECONDS = 2
_FILES_SECONDS = 10.0
_FINISHED_KEY = "_finished_job"

UPDATES = {
    "everything": ("Updating market data", ["run", "-f", "csv"],
                   (Phase.PRICES, Phase.DETAILS, Phase.FILES)),
    "prices": ("Updating prices", ["scan"], (Phase.PRICES,)),
    "details": ("Updating listing details", ["enrich"], (Phase.DETAILS,)),
    "files": ("Writing export files", ["export", "-f", "csv"], (Phase.FILES,)),
}  # fmt: skip


@st.cache_resource
def job_runner(log_dir: str) -> JobRunner:
    """One runner per server process, shared across reruns, pages and browser tabs."""
    return JobRunner(Path(log_dir))


def runner_for(settings: AppSettings) -> JobRunner:
    return job_runner(str(settings.storage.database_path.parent / "logs"))


def run_estimate(settings: AppSettings) -> RunEstimate:
    return estimate(settings, len(settings.stays.resolve(date.today())))


def start_update(settings: AppSettings, kind: str) -> None:
    label, args, phases = UPDATES[kind]
    run = run_estimate(settings)
    estimated = {
        Phase.PRICES: run.scan_minutes * 60,
        Phase.DETAILS: run.enrich_minutes * 60,
        Phase.FILES: _FILES_SECONDS,
    }
    runner_for(settings).start(label, config_path(), args, phases, estimated)


def live_job(runner: JobRunner, render: Callable[[Job | None], None]) -> None:
    """Re-render ``render`` every few seconds while a job runs; refresh data when it ends."""

    @st.fragment(run_every=POLL_SECONDS if runner.busy else None)
    def fragment() -> None:
        job = runner.current
        if job and not job.running and st.session_state.get(_FINISHED_KEY) != job.log_path.name:
            st.session_state[_FINISHED_KEY] = job.log_path.name
            st.cache_data.clear()
            st.rerun()  # every page picks up the new data and the buttons re-enable
        render(job)

    fragment()


def progress_panel(job: Job, runner: JobRunner, *, key: str, compact: bool = False) -> None:
    """Steps, current item, counts, elapsed time and time left of a running job."""
    progress = job.progress()
    current = job.current_phase()
    steps = []
    for phase in job.phases:
        state = progress.get(phase)
        if state and state.finished and phase != current:
            steps.append(f":green[✓ {t(PHASE_LABELS[phase])}]")
        elif phase == current:
            steps.append(f"**● {t(PHASE_LABELS[phase])}**")
        else:
            steps.append(f":gray[○ {t(PHASE_LABELS[phase])}]")
    label = t(job.label)
    st.markdown(f"**{label}**" if not compact else label)
    st.markdown(("  \n" if compact else "  →  ").join(steps))

    state = progress.get(current)
    if state and state.total:
        st.progress(
            state.done / state.total,
            text=t("{done} of {total}", done=state.done, total=state.total),
        )
        st.caption(state.message)
    else:
        st.progress(0.0, text=t("Starting…"))
        st.caption(t("Connecting to Airbnb. The first result can take ~20 seconds."))

    left = job.seconds_left()
    elapsed = t("Elapsed {time}", time=clock(job.elapsed_seconds()))
    st.caption(elapsed + (t(" · about {time} left", time=clock(left)) if left else ""))
    if st.button(t("Stop"), icon=":material/stop_circle:", key=f"stop_{key}", width="stretch"):
        runner.stop()


def sidebar_status() -> None:
    settings = settings_or_none()
    with st.sidebar.container(border=True):
        st.markdown(f"**{t('Market data')}**")
        if settings is None:
            st.caption(t("Not set up yet. Start with Set up my market."))
            return
        runner = runner_for(settings)

        def render(job: Job | None) -> None:
            if job and job.running:
                progress_panel(job, runner, key="sidebar", compact=True)
                return
            st.caption(last_update_text(settings))
            _sample_badge(settings)
            link("collect", t("Update data"), ":material/sync:")

        live_job(runner, render)


def _sample_badge(settings: AppSettings) -> None:
    version = database_version(settings)
    if version is None:
        return
    searches, failed, truncated = sample_status(str(settings.storage.database_path), version)
    if not searches:
        return
    if failed:
        text = t("Partial sample: {failed} of {searches} searches failed",
                 failed=failed, searches=searches)  # fmt: skip
        st.caption(f":orange[{text}]")
    elif truncated:
        text = t("Partial sample: Airbnb truncated {truncated} of {searches} searches",
                 truncated=truncated, searches=searches)  # fmt: skip
        st.caption(f":orange[{text}]")
    else:
        st.caption(f":green[{t('Complete sample: {searches} searches', searches=searches)}]")


def last_update_text(settings: AppSettings) -> str:
    """ "Last update: 27 nov 2026 15:05 · 2 h ago", in the time zone of Settings."""
    moment = last_update(settings)
    when = f"{format_datetime(moment)} · {ago(moment)}" if moment else ago(moment)
    return t("Last update: {when}", when=when)


def clock(seconds: float) -> str:
    minutes, secs = divmod(int(seconds), 60)
    return f"{minutes}:{secs:02d}" if minutes < 60 else f"{minutes // 60} h {minutes % 60:02d} min"
