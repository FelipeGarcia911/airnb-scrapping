"""Which stays the latest data collection covered.

Reports and enrichment must follow what was actually searched, not dates recomputed
from today: check-in dates are relative to the day of the search, so recomputing them a
day later would point at stays that were never collected.
"""

from __future__ import annotations

from airbnb_tracker.application.ports import SearchRunRepository
from airbnb_tracker.domain.value_objects import StayPeriod


def latest_run_stays(runs: SearchRunRepository, conditions_key: str) -> list[StayPeriod]:
    """Stays of the most recent run under these conditions, in the order searched.

    Failed searches count too: their stays keep being reported, from the last valid
    capture of each.
    """
    all_runs = [run for run in runs.all_runs() if run.conditions_key == conditions_key]
    if not all_runs:
        return []
    latest = max(all_runs, key=lambda run: run.started_at).run_id
    return list(dict.fromkeys(run.stay for run in all_runs if run.run_id == latest))
