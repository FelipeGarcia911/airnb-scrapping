"""Which stays the latest data collection covered.

Reports and enrichment must follow what was actually searched, not dates recomputed
from today: check-in dates are relative to the day of the search, so recomputing them a
day later would point at stays that were never collected.
"""

from __future__ import annotations

from airbnb_tracker.application.ports import SearchRunRepository
from airbnb_tracker.domain.value_objects import StayPeriod


def latest_run_stays(runs: SearchRunRepository) -> list[StayPeriod]:
    """Stays of the most recent search run, in the order they were searched."""
    all_runs = runs.all_runs()
    if not all_runs:
        return []
    latest = max(all_runs, key=lambda run: run.started_at).run_id
    return list(dict.fromkeys(run.stay for run in all_runs if run.run_id == latest))
